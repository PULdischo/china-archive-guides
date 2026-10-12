"""Wenshi ziliao index pages: HTML-only prompt (v3), article parsing, and a prompt test.

Prompt v2 asked for HTML *and* a JSON article list; the two disagreed on ~20% of entries
and doubled output tokens. v3 asks for HTML only, with a strict entry structure, and the
articles are parsed from it (with a fallback for plain "title……author(page)" lines).
"""

import base64
import hashlib
import json
import random
import re
from html.parser import HTMLParser

from . import extract, images, markup, ocr, records
from .config import EXPORT, IMAGES, write_json

PROMPT_V3 = """This page is from a wenshi ziliao (文史资料) general index: series titles, issue headings,
and index entries (title, author, page).
Return one JSON object: {{"page_type": "wenshi_index" | "front_matter" | "prose" | "blank" | "other",
"html": "<the whole page transcribed as simple HTML>"}}
HTML rules (follow exactly):
- Series title (e.g. 贵阳文史资料选辑, 六盘水市文史资料选辑) -> <h3>.
- Issue heading (第N辑, 第N期, 总第N辑, 专辑 names) -> <h4>.
- EVERY index entry -> one <li> inside <ul class="toc">, exactly:
  <li><span class="t">TITLE</span><span class="au">AUTHOR</span><span class="pg">PAGE</span></li>
  Omit dot leaders and the parentheses around the page number. Leave a span empty if not printed.
- An entry that wraps onto a second line is ONE <li>; a subtitle starting with —— belongs to the same entry.
- Other lines (编辑, 出版, 印制时间, 字数, notes) -> <p>. Running headers/footers -> <p class="running">.
- Allowed tags only: h3 h4 p ul li span; attribute: class.
- Copy characters exactly as printed (simplified or traditional). Do not translate, normalize or add anything."""
PROMPT_V3_VERSION = "wenshi-v3"


class _Entries(HTMLParser):
    """Articles from v3 HTML (and v2 HTML where it used ul.toc), carrying series/issue in ctx."""

    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx, self.out, self.cur, self.field, self.head, self.buf = ctx, [], None, "t", None, ""
        self.loose = []  # text of <p>/<li> lines without spans, for the leader-line fallback

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if tag in ("h3", "h4"):
            self.head, self.buf = tag, ""
        elif tag == "li":
            self.cur, self.field, self.spans = {"t": "", "au": "", "pg": "", "raw": ""}, "t", False
        elif tag == "span" and self.cur is not None and cls in ("t", "au", "pg"):
            self.field, self.spans = cls, True
        elif tag == "p":
            self.head, self.buf = "p", ""

    def handle_endtag(self, tag):
        if tag in ("h3", "h4") and self.head == tag:
            text, self.head = self.buf.strip(), None
            m = re.search(r"第\s*(\d+)\s*[辑期]", text)
            if tag == "h3":
                self.ctx["series"], self.ctx["issue"] = text, None
            elif m:
                self.ctx["issue"] = int(m.group(1))
        elif tag == "p" and self.head == "p":
            self.loose.append(self.buf.strip())
            self.head = None
        elif tag == "span" and self.cur is not None:
            self.field = "t"
        elif tag == "li" and self.cur is not None:
            c, self.cur = self.cur, None
            if not self.spans:  # model ignored the span structure: try the leader-line rule
                self.loose.append(c["raw"].strip())
                return
            pg = re.sub(r"\D", "", c["pg"])
            title = " ".join(c["t"].split())
            if title:
                self.out.append({"series": self.ctx.get("series"), "issue": self.ctx.get("issue"),
                                 "title": title, "author": " ".join(c["au"].split()) or None,
                                 "page": int(pg) if pg else None})

    def handle_data(self, data):
        if self.cur is not None:
            self.cur[self.field] += data
            self.cur["raw"] += data
        elif self.head:
            self.buf += data


DASH = ("——", "－－", "--", "—")


def _merge_wrapped(entries: list[dict], ctx: dict) -> list[dict]:
    """Models often split a wrapped entry into two items: the main title (no author, no page)
    and a "——subtitle …… author(page)" line. Join only that case: entries with no author
    and no page are often real (indexes print unknown pages as "( )")."""
    out = []
    for e in entries:
        if out and e["title"].startswith(DASH) and out[-1]["author"] is None and out[-1]["page"] is None:
            out[-1] = {**e, "title": out[-1]["title"] + e["title"]}
        else:
            out.append(e)
    return out


def parse_articles(html: str, ctx: dict) -> list[dict]:
    p = _Entries(ctx)
    p.feed(markup.sanitize(html or ""))
    p.close()
    entries = _merge_wrapped(p.out, ctx)
    return entries + extract.articles(p.loose, ctx)  # fallback: "title……author(page)" lines


# ---- prompt test -------------------------------------------------------------------------

REF_ROW = re.compile(r"[（(]\s*\d{1,4}\s*[)）]\s*$")


def _norm(s: str) -> str:
    return re.sub(r"[\s…⋯·.。,，、：:“”\"'《》()（）——\-]", "", s or "")


def run_test(n_pages: int = 40, seed: int = 11) -> None:
    """Compare v2 JSON, v2-HTML parse and v3-HTML parse on a stratified sample of wenshi pages."""
    guides = [g for g in records.build(staged=True) if g["kind"] == "wenshi"]
    rng = random.Random(seed)
    vols = [(g, v) for g in guides for v in g["volumes"] if len(v["pages"]) >= 6]
    by_prov = {}
    for g, v in vols:
        by_prov.setdefault(g["id"], []).append((g, v))
    picks = []
    for gid in rng.sample(sorted(by_prov), min(10, len(by_prov))):
        for g, v in rng.sample(by_prov[gid], min(2, len(by_prov[gid]))):
            picks += [(g, v, p) for p in rng.sample(v["pages"][2:], 2)]
    picks = picks[:n_pages]
    print(f"  {len(picks)} pages from {len({v['manifest'] for _, v, _ in picks})} volumes")

    # Images + RapidOCR reference (cached like the main pipeline).
    with images.http.client() as c:
        for _, _, p in picks:
            f = IMAGES / f"{p['key']}.jpg"
            if not f.exists():
                f.write_bytes(images.http.get(c, images.image_url(p)).content)
    rapid = ocr.RapidOCREngine()
    ds = ocr.DashScopeEngine()

    # API calls in parallel (v2 = current prompt, cached in the main OCR cache; v3 = test cache).
    from concurrent.futures import ThreadPoolExecutor

    def call(item):
        g, v, p = item
        img = IMAGES / f"{p['key']}.jpg"
        h = hashlib.sha256(img.read_bytes()).hexdigest()
        v2 = ocr.load("dashscope", p["key"])
        if not v2 or v2.get("engine") != ds.version or ("error" in v2 and not v2.get("blocked")):
            write_json(ocr.cache_path("dashscope", p["key"]),
                       {"engine": ds.version, "image_sha256": h, "canvas_id": p["canvas_id"], **ds(img, g)})
        v3_path = EXPORT / "wenshi-test" / f"{p['key']}.json"
        if not v3_path.exists():
            r = ds._post(base64.b64encode(img.read_bytes()).decode(), PROMPT_V3, presence_penalty=0.3)
            if r.status_code == 400 and "inappropriate content" in r.text:
                v3 = {"blocked": "moderation"}
            else:
                meta, parsed = ds._parse(r)
                v3 = {**meta, **(parsed or {})}
            write_json(v3_path, v3)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(call, picks))
    print("  API calls done")

    rows = []
    for i, (g, v, p) in enumerate(picks, 1):
        img = IMAGES / f"{p['key']}.jpg"
        h = hashlib.sha256(img.read_bytes()).hexdigest()
        ro = ocr.load("rapidocr", p["key"])
        if not ro or ro.get("image_sha256") != h:
            ro = {"engine": rapid.version, "image_sha256": h, "canvas_id": p["canvas_id"], **rapid(img, g)}
            write_json(ocr.cache_path("rapidocr", p["key"]), ro)
        ref_rows = extract.rows(ro["lines"])
        ref_n = sum(bool(REF_ROW.search(r)) for r in ref_rows)
        ref_text = _norm("".join(ref_rows))

        v2 = ocr.load("dashscope", p["key"])
        v3 = json.loads((EXPORT / "wenshi-test" / f"{p['key']}.json").read_text())

        def score(arts):
            found = sum(_norm(a["title"])[:12] in ref_text for a in arts if a.get("title"))
            return {"n": len(arts), "title_in_ocr": found, "with_page": sum(a.get("page") is not None for a in arts)}

        res = {"guide": g["id"], "volume": v["label"], "page": p["n"], "ref_n": ref_n,
               "v2_json": score(v2.get("articles") or []) if v2.get("html") else None,
               "v2_html": score(parse_articles(v2.get("html"), {})) if v2.get("html") else None,
               "v3": score(parse_articles(v3.get("html"), {})) if v3.get("html") else None,
               "v2_tokens": (v2.get("usage") or {}).get("completion_tokens"),
               "v3_tokens": (v3.get("usage") or {}).get("completion_tokens"),
               "v3_blocked": bool(v3.get("blocked")), "v2_blocked": bool(v2.get("blocked"))}
        rows.append(res)
        if i % 10 == 0:
            print(f"  {i}/{len(picks)}")

    def agg(key):
        rs = [r for r in rows if r[key] and r["ref_n"]]
        n = sum(r[key]["n"] for r in rs)
        return {"pages": len(rs), "entries": n, "ref_entries": sum(r["ref_n"] for r in rs),
                "count_match_pages": sum(abs(r[key]["n"] - r["ref_n"]) <= 1 for r in rs),
                "titles_in_ocr": round(sum(r[key]["title_in_ocr"] for r in rs) / max(n, 1), 3),
                "with_page": round(sum(r[key]["with_page"] for r in rs) / max(n, 1), 3)}

    tok = lambda k: round(sum(r[k] or 0 for r in rows) / max(1, sum(bool(r[k]) for r in rows)))
    summary = {k: agg(k) for k in ("v2_json", "v2_html", "v3")}
    summary["output_tokens_per_page"] = {"v2": tok("v2_tokens"), "v3": tok("v3_tokens")}
    summary["blocked"] = {"v2": sum(r["v2_blocked"] for r in rows), "v3": sum(r["v3_blocked"] for r in rows)}
    write_json(EXPORT / "wenshi-test" / "_results.json", {"summary": summary, "pages": rows})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
