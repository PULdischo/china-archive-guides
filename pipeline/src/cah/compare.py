"""Compare OCR engines on pages both have processed (first look for the bake-off).

Without a hand-keyed gold set this measures agreement, not accuracy: character
similarity of the transcriptions and the number of structured rows each yields.
"""

from difflib import SequenceMatcher

from . import export, ocr, records
from .config import EXPORT

LEADERS = str.maketrans("", "", "…⋯‥·.。 \n")


def run() -> None:
    lines = ["# OCR engine comparison", "",
             "Agreement between engines (not accuracy; that needs the gold set).", "",
             "| Guide | Scan | Similarity | Rows (rapid / vlm) | Fonds | Contents | Articles | VLM tokens |",
             "|---|---|---|---|---|---|---|---|"]
    sims, tokens = [], 0
    for g, v, p in records.all_pages(records.build()):
        a, b = ocr.load("rapidocr", p["key"]), ocr.load("dashscope", p["key"])
        if not a or not b or "error" in b:
            continue
        ra = export.page_content(g, p, {}, ["rapidocr"])
        rb = export.page_content(g, p, {}, ["dashscope"])
        ta = "".join(ra["rows"]).translate(LEADERS)
        tb = "".join(rb["rows"]).translate(LEADERS)
        sim = SequenceMatcher(None, ta, tb, autojunk=False).ratio()
        sims.append(sim)
        used = (b.get("usage") or {}).get("total_tokens", 0)
        tokens += used
        counts = lambda k: f"{len(ra[k])} / {len(rb[k])}"
        lines.append(f"| {g['id']} | {p['n']} | {sim:.3f} | {len(ra['rows'])} / {len(rb['rows'])} | "
                     f"{counts('fonds')} | {counts('toc')} | {counts('articles')} | {used} |")
    if sims:
        lines[3:3] = [f"- Pages compared: {len(sims)}; mean similarity {sum(sims) / len(sims):.3f}; "
                      f"min {min(sims):.3f}", f"- DashScope tokens: {tokens:,} "
                      f"(~{tokens / len(sims):,.0f}/page)", ""]
    (EXPORT / "ocr-compare.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6]))

