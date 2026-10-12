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
    if g.get("province"):
        f["Province"] = [g["province"]["zh"]]
    if g.get("year"):
        f["Published"] = [f"{g['year'] // 10 * 10}s"]
    if g.get("fonds_decades"):
        f["Fonds cover"] = g["fonds_decades"]
    return f


def build_records(guides: list[dict]) -> list[dict]:
    """One record per guide (or wenshi volume) and per scanned page. The "guide" filter is the
    ranking unit: a handbook, or for wenshi ziliao a locality volume (an item can span 4,000+
    pages). meta.href is the unit's page on the site."""
    recs = []
    for g in guides:
        title_text = " ".join(filter(None, [g["title_zh"], g.get("title_romanized"),
                                            " ".join(lazy_pinyin(g["title_zh"] or ""))]))
        filters, sort = _filters(g), {"year": str(g.get("year") or 0)}
        units = ([(g["id"], f"/guides/{g['id']}/", g["title_zh"], g["volumes"])] if g["kind"] != "wenshi" else
                 [(v["id"], f"/wenshi/{v['id']}/", f"{v['label']} · {short_title(g)}", [v]) for v in g["volumes"]])
        for unit_id, href, title, vols in units:
            base = {"filters": {**filters, "guide": [unit_id]}, "sort": sort}
            unit_text = title_text if g["kind"] != "wenshi" else f"{title} {title_text}"
            meta = {"title": title, "year": str(g.get("year") or ""), "guide": unit_id, "href": href,
                    "dpul": g["urls"]["dpul"]}
            # "record" separates unit records (browsing by filter, no query) from page records.
            recs.append({**base, "filters": {**base["filters"], "record": ["guide"]}, "url": href,
                         "content": index_text(unit_text), "meta": {**meta, "kind": "guide", "text": unit_text}})
            for v in vols:
                for p in v["pages"]:
                    text = "\n".join(p.get("rows") or [])
                    if not text.strip():
                        continue
                    recs.append({**base, "filters": {**base["filters"], "record": ["page"]},
                                 "url": f"{href}#p{p['n']}", "content": index_text(text),
                                 "meta": {**meta, "kind": "page", "page": f"Scan {p['n']}", "text": text,
                                          "viewer": p["viewer_url"]}})
    return recs


def short_title(g: dict) -> str:
    """'中国文史资料总目录电子版. 四川卷' -> '四川卷'."""
    t = g["title_zh"] or ""
    return t.split(". ")[-1] if ". " in t else t


# Topical queries a researcher might type (2-4 characters).
TOPICS = ["粮食局", "邮政", "煤矿", "商会", "教育局", "土地改革", "公安局", "海关", "盐务", "铁路",
          "银行", "合作社", "妇女联合会", "卫生局", "法院", "税务", "救济", "航运", "电报", "学校",
          "教会", "红军", "日伪", "国民党", "人民公社", "知识青年", "水利", "兵工厂", "监狱", "侨务"]


def build_eval(guides: list[dict], seed: int = 7) -> list[dict]:
    """Queries with ground truth: guides whose normalized page text contains the normalized query."""
    # Ground truth at the same unit as search ranking: a handbook, or a wenshi locality volume.
    corpus = {}
    for g in guides:
        units = [(g["id"], g["volumes"])] if g["kind"] != "wenshi" else [(v["id"], [v]) for v in g["volumes"]]
        for unit_id, vols in units:
            corpus[unit_id] = normalize("".join("".join(p.get("rows") or []) for v in vols for p in v["pages"]))
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
