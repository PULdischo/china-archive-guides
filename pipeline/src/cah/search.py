"""Search index inputs and the search evaluation set.

Pagefind's own Chinese segmentation splits index text and queries differently
(e.g. 四川省粮食 finds nothing while 四川 finds 27 guides), so we tokenize
ourselves: text is folded to simplified characters, spaces between Han
characters are dropped (OCR artifacts like 全 宗 名 称), and each Han run is
indexed as overlapping unigrams + bigrams. Pagefind 1.5+ re-segments CJK
queries with the browser's Intl.Segmenter, so Han tokens are encoded as ASCII
words (四川 -> h22235x24029) that no segmenter or stemmer will alter. The
browser applies the same normalization and encoding to queries
(site/src/assets/search-core.js) and then verifies exact substrings on the
top candidates.

Outputs (data/export/search/):
  records.jsonl  one record per scanned page and per guide, for the Pagefind Node API
  t2s.json       character map traditional -> simplified (for the browser)
  eval.json      evaluation queries with ground truth (guides containing the string)
"""

import json
import random
import re

from opencc import OpenCC
from pypinyin import lazy_pinyin

from . import records
from .config import EXPORT, read_json, write_json

OUT = EXPORT / "search"
HAN = r"㐀-䶿一-鿿豈-﫿"
_t2s = OpenCC("t2s")
_s2t = OpenCC("s2t")


def t2s_map() -> dict[str, str]:
    """Single-character traditional -> simplified pairs (character-level folding for search)."""
    out = {}
    for cp in [*range(0x3400, 0x4DC0), *range(0x4E00, 0xA000), *range(0xF900, 0xFB00)]:
        ch = chr(cp)
        s = _t2s.convert(ch)
        if len(s) == 1 and s != ch:
            out[ch] = s
    return out


T2S = t2s_map()


def normalize(text: str) -> str:
    """Fold to simplified, lowercase, drop spaces between Han characters."""
    text = "".join(T2S.get(c, c) for c in text or "").lower()
    return re.sub(rf"(?<=[{HAN}])\s+(?=[{HAN}])", "", text)


def tokens(text: str) -> list[str]:
    """Han runs -> unigrams + bigrams; other words (pinyin, English, numbers) as-is."""
    out = []
    for run in re.findall(rf"[{HAN}]+|[a-z0-9]+", normalize(text)):
        if re.match(rf"[{HAN}]", run):
            out += list(run) + [run[i:i + 2] for i in range(len(run) - 1)]
        else:
            out.append(run)
    return out


def encode(token: str) -> str:
    """Han token -> opaque ASCII word (decimal code points), immune to segmenters and stemmers."""
    if re.match(rf"[{HAN}]", token):
        return "h" + "x".join(str(ord(c)) for c in token)
    return token


def index_text(text: str) -> str:
    return " ".join(encode(t) for t in tokens(text))


def _filters(g: dict) -> dict:
    f = {"Type": [{"handbook": "Archival handbook 档案馆指南", "wenshi": "Wenshi ziliao index 文史资料"}[g["kind"]]]}
    if g["places"]:
        f["Province"] = sorted({p["province_zh"] or p["name_zh"] for p in g["places"]})
    if g.get("year"):
        f["Published"] = [f"{g['year'] // 10 * 10}s"]
    if g.get("fonds_decades"):
        f["Fonds cover"] = g["fonds_decades"]
    return f


def build_records(guides: list[dict]) -> list[dict]:
    recs = []
    for g in guides:
        title_text = " ".join(filter(None, [g["title_zh"], g.get("title_romanized"),
                                            " ".join(lazy_pinyin(g["title_zh"] or ""))]))
        # "guide" lets the browser get per-guide match counts without loading fragments.
        base = {"filters": {**_filters(g), "guide": [g["id"]]}, "sort": {"year": str(g.get("year") or 0)}}
        recs.append({**base, "url": f"/guides/{g['id']}/",
                     "content": index_text(title_text),
                     "meta": {"title": g["title_zh"], "year": str(g.get("year") or ""), "guide": g["id"], "kind": "guide",
                              "text": title_text}})
        for v in g["volumes"]:
            for p in v["pages"]:
                text = "\n".join(p.get("rows") or [])
                if not text.strip():
                    continue
                label = f"{v['label']} · " if g["kind"] == "wenshi" else ""
                recs.append({**base, "url": f"/guides/{g['id']}/#p{p['n']}",
                             "content": index_text(text),
                             "meta": {"title": g["title_zh"], "year": str(g.get("year") or ""), "guide": g["id"], "kind": "page",
                                      "page": f"{label}Scan {p['n']}", "text": text}})
    return recs


# Topical queries a researcher might type (2-4 characters).
TOPICS = ["粮食局", "邮政", "煤矿", "商会", "教育局", "土地改革", "公安局", "海关", "盐务", "铁路",
          "银行", "合作社", "妇女联合会", "卫生局", "法院", "税务", "救济", "航运", "电报", "学校",
          "教会", "红军", "日伪", "国民党", "人民公社", "知识青年", "水利", "兵工厂", "监狱", "侨务"]


def build_eval(guides: list[dict], seed: int = 7) -> list[dict]:
    """Queries with ground truth: guides whose normalized page text contains the normalized query."""
    corpus = {g["id"]: normalize("".join("".join(p.get("rows") or []) for v in g["volumes"] for p in v["pages"]))
              for g in guides}
    titles = {g["id"]: " ".join(filter(None, [g["title_zh"], g.get("title_romanized")])).lower() for g in guides}

    def truth(q):
        nq = normalize(q)
        return sorted(gid for gid, text in corpus.items() if nq in text)

    rng = random.Random(seed)
    names = sorted({f["name"] for g in guides for f in g["fonds"]
                    if f.get("extraction") == "vlm" and 6 <= len(f["name"]) <= 18 and re.fullmatch(rf"[{HAN}]+", f["name"])})
    picked = rng.sample(names, 25)
    qs = [{"q": n, "type": "fonds_name"} for n in picked]
    qs += [{"q": t, "type": "topic"} for t in TOPICS]
    qs += [{"q": _s2t.convert(q["q"]), "type": f"{q['type']}_traditional"}
           for q in qs[:10] + qs[25:35] if _s2t.convert(q["q"]) != q["q"]]
    for q in qs:
        q["truth"] = truth(q["q"])
    for term in ["Hunan", "Sichuan Sheng", "Nanjing", "Shanghai Shi", "dang an guan zhi nan",
                 "quan zong", "Guide to", "Xian dang an guan", "Guangzhou", "Tianjin"]:
        qs.append({"q": term, "type": "romanized_title",
                   "truth": sorted(gid for gid, t in titles.items() if term.lower() in t)})
    return [q for q in qs if q["truth"]]


def run() -> None:
    guides = read_json(EXPORT / "site" / "guides.json")
    OUT.mkdir(parents=True, exist_ok=True)
    recs = build_records(guides)
    with (OUT / "records.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    write_json(OUT / "t2s.json", T2S)
    ev = build_eval(guides)
    write_json(OUT / "eval.json", ev)
    print(f"  {len(recs)} search records, {len(T2S)} t2s pairs, {len(ev)} eval queries")
