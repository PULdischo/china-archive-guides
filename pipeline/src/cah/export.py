"""Export the dataset (JSONL per entity) and site-ready JSON/GeoJSON."""

import json
import os
import re
import statistics
from collections import Counter

from opencc import OpenCC
from pypinyin import lazy_pinyin

from . import extract, geo, markup, ocr, records
from .config import EXPORT, write_json

s2t = OpenCC("s2t")


def tidy(row: str) -> str:
    """Collapse dot leaders for display and search (extraction has already used them)."""
    return re.sub(r"\s*[…⋯‥·.。]{3,}\s*", " … ", row).strip()


# Plausible fonds dates: from the founding of the Ming (earliest material in local archives)
# to the present.
EARLIEST, LATEST = 1368, 2030


def _year(v) -> int | None:
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v)
    return v if isinstance(v, int) else None


def clean_fonds_dates(f: dict) -> dict:
    """Plausible date range for an extracted fonds row; raw values and a note are kept."""
    a, b = _year(f.get("date_start")), _year(f.get("date_end"))
    notes = []
    if a is not None and b is not None and 1 <= a <= 38 and 1 <= b <= 38:
        a, b = a + 1911, b + 1911  # 民国纪年: Republic of China years 1-38 = 1912-1949
        notes.append("Republican-era years converted")
    if a is not None and not EARLIEST <= a <= LATEST:
        a = None
        notes.append("implausible start year dropped")
    if b is not None and not EARLIEST <= b <= LATEST:
        b = None
        notes.append("implausible end year dropped")
    if a is not None and b is not None and a > b:
        a, b = b, a
        notes.append("start and end swapped")
    return {"date_start": a, "date_end": b, "date_start_raw": f.get("date_start"),
            "date_end_raw": f.get("date_end"), "date_note": "; ".join(notes) or None}


def cover_buckets(spans: list[tuple[int, int]]) -> list[str]:
    """Search facet values: one bucket for material before 1900, then decades."""
    out = set()
    for a, b in spans:
        if a < 1900:
            out.add("Before 1900")
        out.update(f"{d}s" for d in range(max(a, 1900) // 10 * 10, b + 1, 10) if b >= 1900)
    return sorted(out, key=lambda v: (v != "Before 1900", v))


def pinyin(text: str | None) -> str:
    return " ".join(lazy_pinyin(text)) if text else ""


def write_jsonl(name: str, rows: list[dict]) -> None:
    path = EXPORT / "dataset" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def page_content(g: dict, p: dict, ctx: dict, engines: list[str] | None = None) -> dict:
    """Text + structured rows for one page from the preferred engine, else the RapidOCR baseline."""
    engines = engines or [os.environ.get("CAH_OCR_ENGINE", "rapidocr"), "rapidocr"]
    notes = []
    for engine in dict.fromkeys(engines):
        o = ocr.load(engine, p["key"])
        if o and o.get("blocked"):
            notes.append(f"{engine}: blocked by {o['blocked']}")
        if not o or "error" in o:
            continue
        if engine == "rapidocr":
            rows = extract.rows(o["lines"])
            score = round(statistics.mean(l["score"] for l in o["lines"]), 3) if o["lines"] else None
            structured = ({"fonds": extract.fonds(rows), "toc": extract.toc(rows), "articles": []}
                          if g["kind"] == "handbook" else
                          {"fonds": [], "toc": [], "articles": extract.articles(rows, ctx)})
            return {"rows": rows, "html": None, "engine": o["engine"], "extraction": "rules", "score": score,
                    "notes": notes, **structured}
        if g["kind"] == "wenshi":
            # Articles come from the HTML (more complete than v2's JSON list); ctx carries the
            # series/issue headings across the pages of a volume.
            from .wenshi import parse_articles
            articles = parse_articles(o.get("html"), ctx)
        else:
            articles = []
        fonds = [{**f, "counts": f.get("counts") or []} for f in o.get("fonds", [])]
        if o.get("rescue"):
            notes.append(f"{engine}: rescued ({o['rescue']})")
        # Prompt v2+ returns an HTML transcription; v1 returned plain lines.
        html = markup.sanitize(o["html"]) if o.get("html") else None
        rows = markup.to_lines(html) if html else o.get("lines", [])
        # Completeness check against the RapidOCR baseline: the model sometimes puts a page's
        # content only into the structured rows and leaves the HTML (nearly) empty.
        base = ocr.load("rapidocr", p["key"])
        base_chars = sum(len(l["text"]) for l in base["lines"]) if base else 0
        # Skip short pages (covers), where RapidOCR also reads library barcode stamps.
        if base_chars >= 100 and sum(map(len, rows)) < 0.5 * base_chars:
            if o.get("toc") or o.get("fonds") or o.get("articles"):
                html = markup.from_rows(o.get("toc", []), fonds, articles)
                rows = markup.to_lines(html)
                notes.append(f"{engine}: HTML incomplete, rebuilt from structured rows")
            else:
                notes.append(f"{engine}: transcription incomplete")
                continue
        return {"rows": rows, "html": html, "engine": o["engine"], "extraction": "vlm", "score": None,
                "notes": notes, "fonds": fonds, "toc": o.get("toc", []), "articles": articles,
                "page_type": o.get("page_type")}
    return {"rows": [], "html": None, "engine": None, "extraction": None, "score": None, "notes": notes,
            "fonds": [], "toc": [], "articles": []}


SUFFIX = re.compile(r"(市|县|区|地区|自治州|自治县|盟|旗|政协|文史资料.*)$")


def match_volumes(guides: list[dict], places: list[dict]) -> dict[str, list]:
    """Wenshi locality volumes -> legacy map places (same province, same name minus admin suffix).
    Sets v["place"] and returns {place id: [(guide, volume)]}."""
    out = {}
    for g in guides:
        if g["kind"] != "wenshi" or not g.get("province"):
            continue
        prov = g["province"]["code"]
        candidates = [p for p in places if p["level"] in ("prefecture", "county") and p["code"][:2] == prov]
        for v in g["volumes"]:
            name = SUFFIX.sub("", v["label"] or "")
            hit = next((p for p in candidates if name and SUFFIX.sub("", p["name_zh"] or "") == name), None)
            v["place"] = {k: hit[k] for k in ("id", "level", "code", "name_zh")} if hit else None
            if hit:
                out.setdefault(hit["id"], []).append((g, v))
    return out


# Per-row fields the site doesn't need (the full dataset keeps them). The viewer link is looked up
# from the page instead of repeated on every one of ~1M wenshi article rows.
SITE_ROW_DROP = {"canvas_id", "viewer_url", "engine", "machine_extracted", "guide_id", "date_start_raw",
                 "date_end_raw"}


def site_guide(g: dict) -> dict:
    """Slim copy for the site: one transcription per page (HTML, else plain rows), lean rows."""
    def page(p):
        return {k: v for k, v in p.items() if k != "text_variants" and not (k == "rows" and p.get("html"))}
    out = {**g, "volumes": [{**v, "pages": [page(p) for p in v["pages"]]} for v in g["volumes"]]}
    for key in ("fonds", "toc", "articles"):
        out[key] = [{k: v for k, v in r.items() if k not in SITE_ROW_DROP} for r in g[key]]
    return out


def write_site_guides(guides: list[dict]) -> None:
    """JSON Lines, one guide per line: the whole set (~1 GB as one JSON) exceeds Node's string limit."""
    path = EXPORT / "site" / "guides.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for g in guides:
            f.write(json.dumps(site_guide(g), ensure_ascii=False, separators=(",", ":")) + "\n")
    (EXPORT / "site" / "guides.json").unlink(missing_ok=True)  # superseded


def run() -> None:
    guides = records.build()
    places = geo.places()
    page_rows, fonds_rows, toc_rows, article_rows = [], [], [], []
    scores = []

    for g in guides:
        g["fonds"], g["toc"], g["articles"] = [], [], []
        for v in g["volumes"]:
            ctx = {}
            for p in v["pages"]:
                c = page_content(g, p, ctx)
                rows = p["rows"] = [tidy(r) for r in c["rows"]]
                p["ocr_engine"], p["ocr_mean_score"], p["ocr_notes"] = c["engine"], c["score"], c["notes"]
                p["html"], p["page_type"] = c["html"], c.get("page_type")
                if c["score"] is not None:
                    scores.append(c["score"])
                text = "\n".join(rows)
                # Search variants: traditional forms of the page text (Pagefind has no s/t folding).
                trad = s2t.convert(text)
                p["text_variants"] = trad if trad != text else ""

                ref = {"guide_id": g["id"], "page_n": p["n"], "canvas_id": p["canvas_id"],
                       "viewer_url": p["viewer_url"]}
                prov = {"machine_extracted": True, "extraction": c["extraction"], "engine": c["engine"]}
                for i, f in enumerate(c["fonds"]):
                    rec = {"id": f"{p['key']}-f{i}", **f, **clean_fonds_dates(f), **ref, **prov}
                    g["fonds"].append(rec)
                    fonds_rows.append({**rec, "archive_code": g.get("archive_code")})
                for i, t in enumerate(c["toc"]):
                    rec = {"id": f"{p['key']}-t{i}", **t, **ref, **prov}
                    g["toc"].append(rec)
                    toc_rows.append(rec)
                for i, a in enumerate(c["articles"]):
                    rec = {"id": f"{p['key']}-a{i}", **a, "locality": v["label"], "volume_id": v["id"],
                           **ref, **prov}
                    g["articles"].append(rec)
                    article_rows.append(rec)
                page_rows.append({**ref, "key": p["key"], "volume": v["label"], "volume_id": v["id"],
                                  "label": p["label"],
                                  "image_service": p["image_service"], "text": text, "html": p["html"],
                                  "page_type": p["page_type"],
                                  "ocr_engine": p["ocr_engine"], "ocr_mean_score": p["ocr_mean_score"],
                                  "ocr_notes": p["ocr_notes"]})

        g["province"] = geo.province_of(g)
        g["title_pinyin"] = pinyin(g["title_zh"])
        g["title_zh_trad"] = s2t.convert(g["title_zh"] or "")
        # A single known year counts as a one-year span.
        spans = [(f["date_start"] or f["date_end"], f["date_end"] or f["date_start"]) for f in g["fonds"]
                 if f["date_start"] or f["date_end"]]
        years = sorted({y for span in spans for y in span})
        g["fonds_decades"] = cover_buckets(spans)
        g["fonds_span"] = [years[0], years[-1]] if years else None

    # --- dataset (HF-ready JSONL; Parquet comes with the real release step) ---
    write_jsonl("guides", [{k: v for k, v in g.items() if k not in ("volumes", "fonds", "toc", "articles")}
                           for g in guides])
    write_jsonl("pages", page_rows)
    write_jsonl("fonds", fonds_rows)
    write_jsonl("toc_entries", toc_rows)
    write_jsonl("wenshi_articles", article_rows)
    write_jsonl("places", [{k: v for k, v in p.items() if k != "geometry"} for p in places])

    # --- site ---
    volume_places = match_volumes(guides, places)  # before writing: sets v["place"]
    write_site_guides(guides)
    # Web map. Provinces: geoBoundaries shapes for provinces with guides (no national outline),
    # popup = count + link to the province's guide list. Prefectures/counties: legacy ArcGIS
    # shapes, popup = every guide matched to the place (+ legacy catalog-only links).
    from urllib.parse import quote

    geo.fetch_boundaries()
    by_mms = {g["mms_id"]: g for g in guides}
    prov_count = Counter(g["province"]["code"] for g in guides if g["province"])
    breaks = [3, 7, 12, 20]  # same classes as the home-page map
    features = []
    for pf in geo.province_features({c for c in prov_count if c in geo.PROVINCES}):
        zh, en = geo.PROVINCES[pf["code"]]
        n = prov_count[pf["code"]]
        features.append({"type": "Feature", "geometry": pf["geometry"], "properties": {
            "id": f"province:{pf['code']}", "level": "province", "code": pf["code"], "name_zh": zh,
            "name_pinyin": en, "count": n, "cls": sum(n > b for b in breaks),
            "browse": f"/search/?Province={quote(zh)}"}})
    for p in places:
        if not p["geometry"] or p["level"] not in ("prefecture", "county"):
            continue
        local = [g for g in guides if p["id"] in {x["id"] for x in g["places"]}]
        linked = [{"title": g["title_zh"], "year": g["year"], "href": f"/guides/{g['id']}/", "local": True}
                  for g in sorted(local, key=lambda g: -(g["year"] or 0))]
        linked += [{"title": f"{v['label']} · 文史资料总目录", "year": g["year"], "href": f"/wenshi/{v['id']}/",
                    "local": True} for g, v in volume_places.get(p["id"], [])]
        linked += [{"title": l["title"], "year": l["year"], "href": l["url"], "local": False}
                   for l in p["links"] if l["mms_id"] not in by_mms]
        if not linked:
            continue
        prov = geo.PROVINCES.get(p["code"][:2])
        props = {k: p[k] for k in ("id", "level", "code", "name_zh", "name_pinyin")}
        props.update(guides=linked, province_zh=prov[0] if prov else None,
                     province_count=prov_count.get(p["code"][:2], 0),
                     browse=f"/search/?Province={quote(prov[0])}" if prov else None)
        features.append({"type": "Feature", "properties": props, "geometry": p["geometry"]})
    (EXPORT / "site").mkdir(parents=True, exist_ok=True)
    (EXPORT / "site" / "places.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    # Home-page map: provinces that have guides, shaded by count (5 ordinal classes).
    # Provinces without guides are not drawn (no data -> not on the map; no national outline).
    geo.fetch_boundaries()
    counts = Counter(g["province"]["code"] for g in guides if g["province"])
    breaks = [3, 7, 12, 20]  # class upper bounds: 1-3, 4-7, 8-12, 13-20, 21+
    shapes = geo.province_shapes({c for c in counts if c in geo.PROVINCES})
    provinces = []
    for code, n in counts.most_common():
        info = geo.NATIONAL if code == geo.NATIONAL["code"] else \
            {"code": code, "zh": geo.PROVINCES[code][0], "en": geo.PROVINCES[code][1]}
        provinces.append({**info, "count": n, "cls": sum(n > b for b in breaks),
                          "d": shapes["paths"].get(code), "label": shapes["labels"].get(code)})
    write_json(EXPORT / "site" / "provinces.json", {
        "viewBox": shapes["viewBox"], "breaks": breaks, "provinces": provinces,
        "unplaced": sum(1 for g in guides if not g["province"]),
        "source": "Province boundaries: geoBoundaries (CHN ADM1, public domain, from Wikimedia Commons)"})

    # --- report ---
    mapped_mms = {l["mms_id"] for p in places for l in p["links"]}
    lines = [
        "# Pipeline run report", "",
        f"- Guides: {len(guides)} ({dict(Counter(g['kind'] for g in guides))})",
        f"- Pages: {len(page_rows)}; engines: {dict(Counter(r['ocr_engine'] for r in page_rows))}",
        f"- RapidOCR mean line confidence: {statistics.mean(scores):.3f}" if scores else "- RapidOCR: not used",
        f"- Pages blocked by DashScope moderation (RapidOCR fallback used): "
        f"{sum(any('blocked' in n for n in r['ocr_notes']) for r in page_rows)}",
        f"- Extracted fonds rows: {len(fonds_rows)}; contents entries: {len(toc_rows)}; wenshi article rows: {len(article_rows)}",
        f"- Legacy map: {len(places)} places, {len(mapped_mms)} linked catalog records", "",
        "| Guide | Kind | Year (raw) | Place match | Places | Pages | Fonds | Contents | Articles |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g in guides:
        lines.append(
            f"| {g['id']} {g['title_zh']} | {g['kind']} | {g['year']} ({g['date_raw']}"
            f"{', malformed' if g['date_malformed'] else ''}) | {g['place_match'] or '—'} | "
            f"{', '.join(p['name_zh'] for p in g['places']) or '—'} | "
            f"{sum(len(v['pages']) for v in g['volumes'])} | {len(g['fonds'])} | {len(g['toc'])} | {len(g['articles'])} |")
    (EXPORT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
