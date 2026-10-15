"""Rescue pass for pages DashScope fails on persistently (after ocr.MAX_ATTEMPTS).

Two failure modes seen in the full run:
- Very wide, mostly empty tables (e.g. fonds registers with ~35 document-type columns): the model
  repeats empty cells until the answer is cut off. Rescue: ask for the fonds rows only, as JSON
  (number, name, years, volumes), and generate a clean fonds table as the page's HTML.
- Very dense index pages (e.g. a register of ~80 names): even HTML-only output exceeds the output
  limit. Rescue: split the image at the lightest horizontal band near the middle and transcribe each
  half with the normal wenshi prompt, then join the halves.

Results are written to the normal DashScope cache (same engine version), with "rescue" recording
the strategy, so export, search and the dataset use them like any other page.
"""

import base64
import hashlib
import io

from PIL import Image

from . import markup, ocr, records
from .config import IMAGES, write_json

FONDS_ONLY = """This page is a wide fonds register table (全宗名册) from a Chinese archival handbook (档案馆指南).
Ignore the per-category columns (会计档案, 科技档案, …) and empty rows. Return one JSON object:
{"fonds": [{"fonds_no": str, "name": str, "years": str|null, "volumes": int|null,
            "date_start": int|null, "date_end": int|null}]}
- One item per table row that has a fonds number or name, in order.
- "years": the 年度 / 起止年代 cell as printed; "date_start"/"date_end": its first and last year.
- "volumes": the total 卷 count (合计 卷) if present.
- Copy characters exactly as printed. Do not add rows that are not on the page."""


def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def split_point(img: Image.Image, lo: float = 0.4, hi: float = 0.6) -> int:
    """Row index of the lightest horizontal band between lo and hi of the page height."""
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()
    step = max(1, w // 300)  # sample columns for speed
    best, best_y = -1.0, h // 2
    for y in range(int(h * lo), int(h * hi)):
        band = sum(px[x, yy] for yy in range(max(0, y - 2), min(h, y + 3)) for x in range(0, w, step))
        if band > best:
            best, best_y = band, y
    return best_y


def rescue_fonds(ds: ocr.DashScopeEngine, img: Image.Image) -> dict:
    meta, parsed = ds._parse(ds._post(_b64(img), FONDS_ONLY))
    if parsed is None:
        return meta
    fonds = [{"fonds_no": str(f.get("fonds_no") or ""), "name": f.get("name") or "",
              "counts": [f["volumes"]] if isinstance(f.get("volumes"), int) else [],
              "date_start": f.get("date_start"), "date_end": f.get("date_end")}
             for f in parsed.get("fonds") or [] if f.get("name") or f.get("fonds_no")]
    return {**meta, "page_type": "fonds_table", "fonds": fonds, "toc": [], "articles": [],
            "html": markup.from_rows([], fonds, []), "rescue": "fonds_only"}


def rescue_split(ds: ocr.DashScopeEngine, img: Image.Image, prompt: str) -> dict:
    y = split_point(img)
    halves = [img.crop((0, 0, img.width, y)), img.crop((0, y, img.width, img.height))]
    parts, usage = [], 0
    for half in halves:
        meta, parsed = ds._parse(ds._post(_b64(half), prompt, presence_penalty=0.3))
        usage += (meta.get("usage") or {}).get("total_tokens", 0)
        if parsed is None:
            return {**meta, "rescue": "split_failed"}
        parts.append(parsed.get("html") or "")
    return {"model": ds.model, "usage": {"total_tokens": usage}, "finish_reason": "stop",
            "page_type": "wenshi_index", "html": "".join(parts), "toc": [], "fonds": [], "articles": [],
            "rescue": f"split_at_{y}px"}


def run() -> None:
    from .wenshi import PROMPT_V3

    ds = ocr.DashScopeEngine()
    todo = []
    for g, _, p in records.all_pages(records.build()):
        o = ocr.load("dashscope", p["key"])
        if o and "error" in o and not o.get("blocked") and o.get("attempts", 1) >= ocr.MAX_ATTEMPTS:
            todo.append((g, p, o))
    print(f"  {len(todo)} persistently failing pages")
    for g, p, o in todo:
        path = IMAGES / f"{p['key']}.jpg"
        img = Image.open(path)
        result = rescue_split(ds, img, PROMPT_V3) if g["kind"] == "wenshi" else rescue_fonds(ds, img)
        ok = "error" not in result
        if ok:
            write_json(ocr.cache_path("dashscope", p["key"]),
                       {"engine": ds.version_for(g), "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "canvas_id": p["canvas_id"], **result})
        detail = (f"{len(result.get('fonds', []))} fonds rows" if g["kind"] != "wenshi"
                  else f"{len(markup.to_lines(result.get('html', '')))} lines") if ok else result.get("error", "")[:80]
        print(f"  {'rescued' if ok else 'FAILED '} {g['id']} scan {p['n']} ({result.get('rescue')}): {detail}")
