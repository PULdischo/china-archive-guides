"""OCR quality audit (PLAN.md §5b): per-page flags, an independent RapidOCR baseline, a review sample.

  uv run cah audit                 flags for every page -> data/export/audit/ocr-audit.csv + summary.md
  uv run cah audit --baseline 1000 also run RapidOCR on a stratified sample of pages that lack it
  uv run cah audit --sample 150    also write a stratified human-review sheet + REVIEW-GUIDE.md

Flags are signals for where to look, not verdicts: e.g. a fonds number that repeats can be a real
restart of the numbering. Agreement with RapidOCR is reported only where both engines read the page.
"""

import csv
import json
import random
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from . import extract, ocr, records
from .config import EXPORT, IMAGES

OUT = EXPORT / "audit"
HAN = re.compile(r"[㐀-鿿]")
CIRCLED = re.compile(r"[①-⓿❶-➓]")
IMPRINT = re.compile(r"(出\s*版|编\s*辑|主\s*办|印\s*刷|[：:])")
STRUCTURED = {"toc", "fonds_table", "wenshi_index", "holdings_table", "fonds_list"}
NORM = str.maketrans("", "", "…⋯‥·.。 \n\t　")

# Flag -> short description (also the column order of the CSV and the summary).
FLAGS = {
    "fallback": "read by RapidOCR (model refused the page)",
    "rescued": "rescued with a targeted request (fonds-only or split page)",
    "truncated_retry": "first answer exceeded the output limit; HTML-only retry used",
    "short_structured": "structured page with < 80 characters of text",
    "rare_chars": "Han characters outside GB2312 (rare names, traditional, or misreads)",
    "circled_numerals": "circled numerals (footnote markers), possibly glued to numbers",
    "page_backwards": "wenshi: printed page number lower than the previous entry's in the same issue",
    "page_implausible": "wenshi: printed page number > 2000 (e.g. a range read as one number)",
    "no_page": "wenshi/contents entries without a page number",
    "series_imprint": "wenshi: series set from an imprint/editor line (出版/编辑…)",
    "fonds_repeat": "fonds number equal to the previous row's",
    "fonds_jump": "fonds number > 20 above the previous row's",
    "date_cleaned": "fonds dates changed by cleaning (ROC years, swaps, implausible years)",
    "low_agreement": "transcription differs from RapidOCR (similarity < 0.8)",
}


def _text(p: dict) -> str:
    if p.get("html"):
        return re.sub(r"<[^>]+>", "\n", p["html"])
    return "\n".join(p.get("rows") or [])


def _gb_bad(s: str) -> int:
    n = 0
    for ch in s:
        if HAN.match(ch):
            try:
                ch.encode("gb2312")
            except UnicodeEncodeError:
                n += 1
    return n


def _site_guides():
    with (EXPORT / "site" / "guides.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def page_rows() -> list[dict]:
    rows = []
    for g in _site_guides():
        by_page = defaultdict(lambda: {"fonds": [], "toc": [], "articles": []})
        for k in ("fonds", "toc", "articles"):
            for r in g[k]:
                by_page[r["page_n"]][k].append(r)
        # Fonds-number sequence across the guide, in page/row order.
        prev = None
        fonds_seq = defaultdict(lambda: {"fonds_repeat": 0, "fonds_jump": 0})
        for f in sorted(g["fonds"], key=lambda r: r["page_n"]):
            no = str(f.get("fonds_no") or "")
            if re.fullmatch(r"[0-9]+", no):
                n = int(no)
                if prev is not None:
                    fonds_seq[f["page_n"]]["fonds_repeat"] += n == prev
                    fonds_seq[f["page_n"]]["fonds_jump"] += (n - prev) > 20
                prev = n
        for v in g["volumes"]:
            for p in v["pages"]:
                text = _text(p)
                body = "".join(l.strip() for l in text.split("\n"))
                rec = by_page[p["n"]]
                engine = p.get("ocr_engine") or ""
                notes = " ".join(p.get("ocr_notes") or [])
                arts = rec["articles"]
                back = 0
                last = {}
                for a in arts:
                    pg = a.get("page")
                    if isinstance(pg, int) and pg < 2000:
                        key = (a.get("series"), a.get("issue"))
                        back += key in last and pg < last[key]
                        last[key] = pg
                flags = {
                    "fallback": int(engine.startswith("rapidocr")),
                    "rescued": int("rescued" in notes),
                    "truncated_retry": 0,  # filled from the DashScope cache below
                    "short_structured": int(len(body) < 80 and (p.get("page_type") in STRUCTURED)),
                    "rare_chars": _gb_bad(body),
                    "circled_numerals": len(CIRCLED.findall(body)),
                    "page_backwards": back,
                    "page_implausible": sum(isinstance(a.get("page"), int) and a["page"] > 2000 for a in arts),
                    "no_page": sum(a.get("page") is None for a in arts) + sum(t.get("page") is None for t in rec["toc"]),
                    "series_imprint": sum(bool(a.get("series") and IMPRINT.search(a["series"])) for a in arts),
                    **fonds_seq[p["n"]],
                    "date_cleaned": sum(bool(f.get("date_note")) for f in rec["fonds"]),
                    "low_agreement": 0,
                }
                rows.append({
                    "guide_id": g["id"], "kind": g["kind"], "guide_title": g["title_zh"],
                    "province": (g.get("province") or {}).get("zh"), "year": g.get("year"),
                    "volume_id": v["id"], "volume": v["label"] if g["kind"] == "wenshi" else "",
                    "page_n": p["n"], "key": p["key"], "page_type": p.get("page_type") or "(fallback)",
                    "engine": engine, "chars": len(body), "fonds": len(rec["fonds"]), "toc": len(rec["toc"]),
                    "articles": len(arts), "agreement": "", **flags,
                    "url": (f"/wenshi/{v['id']}/#p{p['n']}" if g["kind"] == "wenshi" else f"/guides/{g['id']}/#p{p['n']}"),
                    "viewer_url": p.get("viewer_url"), "image": f"{p['image_service']}/full/full/0/default.jpg",
                    "_body": body,
                })
    return rows


def add_engine_signals(rows: list[dict]) -> None:
    """Truncation retries from the DashScope cache; agreement where RapidOCR also read the page."""
    for r in rows:
        ds = ocr.load("dashscope", r["key"])
        if ds and ds.get("retry"):
            r["truncated_retry"] = 1
        ro = ocr.load("rapidocr", r["key"])
        if ro and ro.get("lines") and not r["fallback"]:
            a = "".join(extract.rows(ro["lines"])).translate(NORM)
            b = r["_body"].translate(NORM)
            sim = SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0
            r["agreement"] = round(sim, 3)
            r["low_agreement"] = int(sim < 0.8)


def write(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cols = [k for k in rows[0] if not k.startswith("_")] + ["score"]
    for r in rows:
        r["score"] = sum(1 for k in FLAGS if r[k])
    with (OUT / "ocr-audit.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    lines = ["# OCR audit summary", "", f"{total:,} pages. A page is *flagged* if any signal is non-zero; "
             "signals point to where to look, they are not verdicts.", "",
             "| Signal | Pages | Handbook pages | Wenshi pages | Occurrences | Meaning |", "|---|---|---|---|---|---|"]
    for k, desc in FLAGS.items():
        hit = [r for r in rows if r[k]]
        lines.append(f"| `{k}` | {len(hit):,} ({len(hit) / total:.1%}) | {sum(r['kind'] == 'handbook' for r in hit):,} | "
                     f"{sum(r['kind'] == 'wenshi' for r in hit):,} | {sum(r[k] for r in hit):,} | {desc} |")
    flagged = sum(r["score"] > 0 for r in rows)
    agree = [r["agreement"] for r in rows if r["agreement"] != ""]
    lines += ["", f"- Pages with any signal: {flagged:,} ({flagged / total:.1%}); with ≥3 signals: "
              f"{sum(r['score'] >= 3 for r in rows):,}.",
              f"- Agreement with RapidOCR measured on {len(agree):,} pages: mean {sum(agree) / max(len(agree), 1):.3f}; "
              f"< 0.8 on {sum(a < 0.8 for a in agree):,}." if agree else "- No RapidOCR overlap measured yet.",
              "", "## By page type", "", "| Page type | Pages | Flagged | Mean signals |", "|---|---|---|---|"]
    by_type = defaultdict(list)
    for r in rows:
        by_type[(r["kind"], r["page_type"])].append(r)
    for (kind, t), rs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        lines.append(f"| {kind} · {t} | {len(rs):,} | {sum(r['score'] > 0 for r in rs):,} | "
                     f"{sum(r['score'] for r in rs) / len(rs):.2f} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:len(FLAGS) + 9]))


def baseline(rows: list[dict], n: int, seed: int = 17) -> None:
    """RapidOCR on a stratified random sample of model-read pages without a RapidOCR reading."""
    import hashlib

    from .config import write_json

    pool = defaultdict(list)
    for r in rows:
        if not r["fallback"] and not ocr.load("rapidocr", r["key"]):
            pool[(r["kind"], r["page_type"])].append(r)
    total = sum(len(v) for v in pool.values())
    rng = random.Random(seed)
    picks = []
    for stratum, rs in sorted(pool.items()):
        k = max(1, round(n * len(rs) / total))
        picks += rng.sample(rs, min(k, len(rs)))
    print(f"  baseline: RapidOCR on {len(picks)} pages across {len(pool)} strata")
    engine = ocr.RapidOCREngine()
    for i, r in enumerate(picks, 1):
        img = IMAGES / f"{r['key']}.jpg"
        if not img.exists():
            continue
        result = engine(img, {"kind": r["kind"]})
        write_json(ocr.cache_path("rapidocr", r["key"]),
                   {"engine": engine.version, "image_sha256": hashlib.sha256(img.read_bytes()).hexdigest(),
                    "canvas_id": None, "baseline_sample": True, **result})
        if i % 50 == 0:
            print(f"  {i}/{len(picks)}")


TAXONOMY = {  # reviewer columns: count of each error type on the page
    "err_char": "Wrong character (similar shape, e.g. 已/己, 未/末, 戊/戌), or wrong digit",
    "err_row_missing": "A line/row/entry on the scan is missing from the transcription",
    "err_row_extra": "A row is duplicated, or text appears that is not on the scan (hallucination)",
    "err_merge_split": "Two entries merged into one, or one wrapped entry split in two",
    "err_column": "Table cell in the wrong column (e.g. a date in the counts column), or rows misaligned",
    "err_number": "Year, page number, fonds number or count misread",
    "err_order": "Reading order wrong (e.g. two-column page read across)",
    "err_header": "Running head, imprint, editor or page-number line mixed into the entries",
    "err_normalized": "Text changed from what is printed (traditional/simplified/variant forms 'corrected')",
    "err_structure": "Wrong structure: series/issue/section headings missing or wrong; fields assigned wrongly",
}


def sample(rows: list[dict], n: int, site_url: str, seed: int = 23) -> None:
    """Stratified review sheet: ~40% from flagged pages (spread across signals), ~60% across page types."""
    rng = random.Random(seed)
    chosen, seen = [], set()

    def take(rs, k):
        rs = [r for r in rs if r["key"] not in seen]
        for r in rng.sample(rs, min(k, len(rs))):
            seen.add(r["key"]); chosen.append(r)

    flagged_n = round(n * 0.4)
    signals = [k for k in FLAGS if any(r[k] for r in rows)]
    per = max(1, flagged_n // len(signals))
    for k in signals:
        take([r for r in rows if r[k]], per)
    strata = defaultdict(list)
    for r in rows:
        strata[(r["kind"], r["page_type"], "fallback" if r["fallback"] else "model")].append(r)
    rest = n - len(chosen)
    big = [s for s in strata if len(strata[s]) >= 20]
    for s in big:
        take(strata[s], max(1, round(rest * len(strata[s]) / sum(len(strata[b]) for b in big))))
    chosen = chosen[:n]
    rng.shuffle(chosen)

    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["review_id", "guide_title", "volume", "page_n", "page_type", "engine", "signals", "site_page", "dpul_viewer",
            "image", "reviewer", "quality_1_5", *TAXONOMY, "gold_candidate_y_n", "notes", "guide_id", "key"]
    with (OUT / "review-sample.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, r in enumerate(chosen, 1):
            sig = ", ".join(k for k in FLAGS if r[k])
            w.writerow([f"R{i:03d}", r["guide_title"], r["volume"], r["page_n"], r["page_type"], r["engine"], sig,
                        site_url.rstrip("/") + r["url"], r["viewer_url"], r["image"], "", "", *([""] * len(TAXONOMY)),
                        "", "", r["guide_id"], r["key"]])
    guide = ["# OCR review guide", "",
             f"You are reviewing {len(chosen)} scanned pages. For each row of `review-sample.csv`:", "",
             "1. Open `site_page` (the transcription next to a thumbnail) and `dpul_viewer` or `image` (the full scan).",
             "2. Compare the transcription with the scan, top to bottom.",
             "3. Count errors of each type below and enter the counts in the matching column (0 if none).",
             "4. Give an overall `quality_1_5` (5 = no errors, 4 = minor errors that don't affect finding the item, "
             "3 = some entries wrong, 2 = many errors, 1 = unusable).",
             "5. Mark `gold_candidate_y_n` = y for typical, legible pages worth hand-keying as reference text.",
             "6. Use `notes` for anything else (e.g. print quality, handwriting, stamps, show-through).", "",
             "Count each error once, at the smallest unit (a wrong character inside a correct row is `err_char`, "
             "not a missing row). Punctuation and dot leaders don't count.", "", "| Column | Error type |", "|---|---|"]
    guide += [f"| `{k}` | {v} |" for k, v in TAXONOMY.items()]
    guide += ["", "The `signals` column shows why a page may have been chosen; review it like any other page.",
              "About 60% of the sample is drawn at random across page types, so many pages will have no signal."]
    (OUT / "REVIEW-GUIDE.md").write_text("\n".join(guide) + "\n", encoding="utf-8")
    print(f"  review sample: {len(chosen)} pages -> {OUT / 'review-sample.csv'} (+ REVIEW-GUIDE.md)")
    print("  by page type:", dict(Counter(f"{r['kind']}·{r['page_type']}" for r in chosen)))


def run(baseline_n: int = 0, sample_n: int = 0, site_url: str = "") -> None:
    rows = page_rows()
    if baseline_n:
        baseline(rows, baseline_n)
    add_engine_signals(rows)
    write(rows)
    if sample_n:
        sample(rows, sample_n, site_url)
