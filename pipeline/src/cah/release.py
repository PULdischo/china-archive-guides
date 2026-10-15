"""Dataset release: typed Parquet tables + dataset card + site bundle, and upload to Hugging Face.

  uv run cah release                          -> data/release/  (local, nothing uploaded)
  uv run cah publish --repo ORG/NAME          -> upload data/release/ and tag the version

Images are never included: pages carry IIIF image-service URLs and DPUL viewer links instead.
"""

import gzip
import json
import shutil
from collections import Counter
from datetime import date
from pathlib import Path
from string import Template

import pyarrow as pa
import pyarrow.parquet as pq

from .config import EXPORT, ROOT

VERSION = "0.1.0"
RELEASE = ROOT / "data" / "release"
CARD_TEMPLATE = Path(__file__).with_name("dataset_card.md")
SITE_FILES = {  # site build inputs, published alongside the dataset for the Pages workflow
    "site/guides.jsonl.gz": EXPORT / "site" / "guides.jsonl",
    "site/records.jsonl.gz": EXPORT / "search" / "records.jsonl",
    "site/t2s.json": EXPORT / "search" / "t2s.json",
    "site/places.geojson": EXPORT / "site" / "places.geojson",
    "site/provinces.json": EXPORT / "site" / "provinces.json",
}


def _rows(name: str):
    with (EXPORT / "dataset" / f"{name}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _int(v):
    """Integer or None. Values outside int32 are OCR artifacts (digits of adjacent cells run together)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        v = int(v)
    return v if isinstance(v, int) and -2**31 < v < 2**31 else None


def _str(v):
    return None if v is None else str(v)


S, I, B, F = pa.string(), pa.int32(), pa.bool_(), pa.float32()
L = pa.list_
SCHEMAS = {
    "guides": pa.schema([
        ("id", S), ("kind", S), ("title_zh", S), ("title_romanized", S), ("title_full", L(S)),
        ("editor", L(S)), ("publisher", L(S)), ("year", I), ("date_raw", S), ("date_malformed", B),
        ("call_number", S), ("extent", S), ("spatial", L(S)), ("subjects", L(S)), ("rights", S),
        ("admin_code", S), ("archive_code", S), ("province_code", S), ("province_zh", S), ("province_en", S),
        ("place_ids", L(S)), ("place_match", S), ("volumes_total", I), ("volumes_restricted", L(S)),
        ("fonds_date_start", I), ("fonds_date_end", I),
        ("ark", S), ("mms_id", S), ("figgy_id", S), ("dpul_url", S), ("catalog_url", S), ("ark_url", S),
        ("manifest_url", S), ("dpul_updated_at", S)]),
    "volumes": pa.schema([
        ("id", S), ("guide_id", S), ("index", I), ("label", S), ("canvas_count", I), ("pages", I),
        ("place_id", S), ("place_name_zh", S), ("manifest_url", S)]),
    "pages": pa.schema([
        ("guide_id", S), ("volume_id", S), ("page_n", I), ("canvas_id", S), ("canvas_label", S),
        ("page_type", S), ("text", S), ("html", S), ("ocr_engine", S), ("ocr_notes", L(S)),
        ("image_service", S), ("viewer_url", S)]),
    "fonds": pa.schema([
        ("id", S), ("guide_id", S), ("archive_code", S), ("page_n", I), ("canvas_id", S),
        ("fonds_no", S), ("name", S), ("counts", L(I)), ("date_start", I), ("date_end", I),
        ("date_start_raw", S), ("date_end_raw", S), ("date_note", S), ("extraction", S), ("engine", S)]),
    "toc_entries": pa.schema([
        ("id", S), ("guide_id", S), ("page_n", I), ("canvas_id", S), ("heading", S), ("section", S),
        ("page", I), ("extraction", S), ("engine", S)]),
    "wenshi_articles": pa.schema([
        ("id", S), ("guide_id", S), ("volume_id", S), ("locality", S), ("page_n", I), ("canvas_id", S),
        ("series", S), ("issue", I), ("title", S), ("author", S), ("page", I), ("section", S),
        ("extraction", S), ("engine", S)]),
    "places": pa.schema([
        ("id", S), ("level", S), ("code", S), ("name_zh", S), ("name_pinyin", S), ("name_en", S),
        ("province_zh", S), ("province_en", S),
        ("legacy_links", L(pa.struct([("title", S), ("year", I), ("mms_id", S), ("url", S)])))]),
}


def _guides(site_guides: list[dict]):
    for g in site_guides:
        urls, prov = g.get("urls") or {}, g.get("province") or {}
        span = g.get("fonds_span") or [None, None]
        yield {**{k: g.get(k) for k in ("id", "kind", "title_zh", "title_romanized", "title_full", "editor",
                                        "publisher", "year", "date_raw", "date_malformed", "call_number",
                                        "extent", "spatial", "subjects", "rights", "admin_code", "archive_code",
                                        "place_match", "volumes_total", "ark", "mms_id", "figgy_id",
                                        "dpul_updated_at")},
               "province_code": prov.get("code"), "province_zh": prov.get("zh"), "province_en": prov.get("en"),
               "place_ids": [p["id"] for p in g.get("places") or []],
               "volumes_restricted": [r.get("label") for r in g.get("volumes_restricted") or []],
               "fonds_date_start": span[0], "fonds_date_end": span[1],
               "dpul_url": urls.get("dpul"), "catalog_url": urls.get("catalog"), "ark_url": urls.get("ark"),
               "manifest_url": urls.get("manifest")}


def _volumes(site_guides: list[dict]):
    for g in site_guides:
        for v in g["volumes"]:
            place = v.get("place") or {}
            yield {"id": v["id"], "guide_id": g["id"], "index": v["index"], "label": v["label"],
                   "canvas_count": v["canvas_count"], "pages": len(v["pages"]), "place_id": place.get("id"),
                   "place_name_zh": place.get("name_zh"), "manifest_url": v["manifest"]}


def _pages():
    for r in _rows("pages"):
        yield {**{k: r.get(k) for k in ("guide_id", "volume_id", "page_n", "canvas_id", "page_type", "text",
                                        "html", "ocr_engine", "ocr_notes", "image_service", "viewer_url")},
               "canvas_label": r.get("label")}


def _fonds():
    for r in _rows("fonds"):
        yield {**{k: r.get(k) for k in ("id", "guide_id", "archive_code", "page_n", "canvas_id", "name",
                                        "date_note", "extraction", "engine")},
               "fonds_no": _str(r.get("fonds_no")), "counts": [c for c in map(_int, r.get("counts") or [])
                                                               if c is not None],
               "date_start": _int(r.get("date_start")), "date_end": _int(r.get("date_end")),
               "date_start_raw": _str(r.get("date_start_raw")), "date_end_raw": _str(r.get("date_end_raw"))}


def _toc():
    for r in _rows("toc_entries"):
        yield {**{k: _str(r.get(k)) for k in ("id", "guide_id", "canvas_id", "heading", "section", "extraction",
                                              "engine")}, "page_n": _int(r.get("page_n")), "page": _int(r.get("page"))}


def _articles():
    for r in _rows("wenshi_articles"):
        yield {**{k: _str(r.get(k)) for k in ("id", "guide_id", "volume_id", "locality", "canvas_id", "series",
                                              "title", "author", "section", "extraction", "engine")},
               "page_n": _int(r.get("page_n")), "issue": _int(r.get("issue")), "page": _int(r.get("page"))}


def _places():
    for r in _rows("places"):
        yield {**{k: _str(r.get(k)) for k in ("id", "level", "code", "name_zh", "name_pinyin", "name_en",
                                              "province_zh", "province_en")},
               "legacy_links": [{"title": l.get("title"), "year": _int(l.get("year")), "mms_id": l.get("mms_id"),
                                 "url": l.get("url")} for l in r.get("links") or []]}


def _write(name: str, rows, batch: int = 50_000) -> int:
    """Stream rows into data/{name}.parquet in batches (the articles table has ~1M rows)."""
    schema, n = SCHEMAS[name], 0
    path = RELEASE / "data" / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(path, schema, compression="zstd") as w:
        buf = []
        for r in rows:
            buf.append({f.name: r.get(f.name) for f in schema})
            if len(buf) >= batch:
                w.write_table(pa.Table.from_pylist(buf, schema=schema)); n += len(buf); buf = []
        if buf:
            w.write_table(pa.Table.from_pylist(buf, schema=schema)); n += len(buf)
    return n


DEFAULT_REPO = "PULdischo/china-archive-guides"  # matches the GitHub repository; change with --repo


def _stats(site_guides: list[dict], counts: dict) -> dict:
    pages = pq.read_table(RELEASE / "data" / "pages.parquet", columns=["ocr_engine", "page_type", "ocr_notes"])
    engines = Counter(pages.column("ocr_engine").to_pylist())
    notes = pages.column("ocr_notes").to_pylist()
    blocked = sum(any("blocked" in n for n in ns or []) for ns in notes)
    kinds = Counter(g["kind"] for g in site_guides)
    years = [g["year"] for g in site_guides if g.get("year")]
    wenshi_pages = sum(len(v["pages"]) for g in site_guides if g["kind"] == "wenshi" for v in g["volumes"])
    ds = sum(n for e, n in engines.items() if e and e.startswith("dashscope"))
    fmt = lambda n: f"{n:,}"
    return {
        "version": VERSION, "date": date.today().isoformat(),
        "n_guides": fmt(counts["guides"]), "n_handbooks": fmt(kinds["handbook"]), "n_wenshi": fmt(kinds["wenshi"]),
        "n_volumes": fmt(counts["volumes"]),
        "n_wenshi_volumes": fmt(sum(len(g["volumes"]) for g in site_guides if g["kind"] == "wenshi")),
        "n_pages": fmt(counts["pages"]), "n_wenshi_pages": fmt(wenshi_pages),
        "n_handbook_pages": fmt(counts["pages"] - wenshi_pages),
        "n_fonds": fmt(counts["fonds"]), "n_toc": fmt(counts["toc_entries"]),
        "n_articles": fmt(counts["wenshi_articles"]), "n_places": fmt(counts["places"]),
        "n_provinces": fmt(len({g["province"]["code"] for g in site_guides if g.get("province")})),
        "year_min": min(years), "year_max": max(years),
        "pct_dashscope": f"{ds / counts['pages']:.1%}", "n_rapidocr": fmt(engines.get(
            next((e for e in engines if e and e.startswith("rapidocr")), ""), 0)),
        "n_blocked": fmt(blocked), "pct_blocked": f"{blocked / counts['pages']:.1%}",
        "restricted": "; ".join(f"{g['title_zh']}: {r['label']}" for g in site_guides
                                for r in g.get("volumes_restricted") or []) or "none",
    }


def run(repo: str = DEFAULT_REPO) -> None:
    if RELEASE.exists():
        shutil.rmtree(RELEASE)
    with (EXPORT / "site" / "guides.jsonl").open(encoding="utf-8") as f:
        site_guides = [json.loads(line) for line in f if line.strip()]

    builders = {"guides": lambda: _guides(site_guides), "volumes": lambda: _volumes(site_guides), "pages": _pages,
                "fonds": _fonds, "toc_entries": _toc, "wenshi_articles": _articles, "places": _places}
    counts = {}
    for name, build in builders.items():
        counts[name] = _write(name, build())
        print(f"  {name:16} {counts[name]:>9,} rows  "
              f"{(RELEASE / 'data' / f'{name}.parquet').stat().st_size / 1e6:7.1f} MB")

    for dest, src in SITE_FILES.items():
        out = RELEASE / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        if dest.endswith(".gz"):
            with src.open("rb") as fi, gzip.open(out, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
        else:
            shutil.copy(src, out)
    print(f"  site bundle      {sum(p.stat().st_size for p in (RELEASE / 'site').iterdir()) / 1e6:7.1f} MB")

    stats = {**_stats(site_guides, counts), "repo": repo}
    (RELEASE / "README.md").write_text(Template(CARD_TEMPLATE.read_text(encoding="utf-8")).substitute(stats),
                                       encoding="utf-8")
    (RELEASE / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  dataset card     {RELEASE / 'README.md'}  (v{VERSION})")


def publish(repo: str, private: bool = False) -> None:
    """Upload data/release/ to a Hugging Face dataset repo and tag it with the version."""
    import os

    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set (add a write token to .env)")
    if not (RELEASE / "README.md").exists():
        raise SystemExit("No release built; run `uv run cah release` first")
    api = HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", private=private, exist_ok=True)
    commit = api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=RELEASE,
                               commit_message=f"Release v{VERSION}")
    api.create_tag(repo, repo_type="dataset", tag=f"v{VERSION}", revision=commit.oid, exist_ok=True)
    print(f"  published https://huggingface.co/datasets/{repo} (tag v{VERSION})")
