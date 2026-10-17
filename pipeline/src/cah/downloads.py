"""Downloads for people who don't use Parquet: flat CSVs, a ZIP of everything, an SQLite database.

Written by `cah release` into data/release/downloads/ (published with the dataset on Hugging Face,
linked from the site's Data page):

  csv/guides.csv, volumes.csv, fonds.csv, toc_entries.csv, places.csv
  csv/wenshi_articles/{province}.csv   one per province (Excel's limit is ~1M rows; the set is ~1M)
  wenshi_articles.csv.gz               all wenshi index entries in one file
  china-archive-guides-csv.zip         all CSVs + DATA-DICTIONARY.md + README + LICENSE
  china-archive-guides.sqlite.zip      every table (page text, not HTML) + joined views
  manifest.json                        file, rows, bytes, sha256 (feeds the Data page and the card)

CSVs are "flat": each row carries the guide title, province and links, so a spreadsheet user needs no
joins. They are UTF-8 with a byte-order mark so Excel shows Chinese correctly. Lists are joined with "; ".
"""

import csv
import gzip
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pyarrow.parquet as pq

# Flat CSV columns: (name, description). Also the data dictionary on the site and in the ZIP.
COLUMNS = {
    "guides": [
        ("id", "DPUL identifier; https://dpul.princeton.edu/ealtoc/catalog/{id}"),
        ("type", "handbook (archival handbook 档案馆指南) or wenshi (wenshi ziliao index 文史资料)"),
        ("title_zh", "Title in Chinese"), ("title_romanized", "Title in pinyin romanization (catalog)"),
        ("year", "Publication year (normalized from the catalog date)"),
        ("province_zh", "Province (Chinese)"), ("province_en", "Province (English)"),
        ("admin_code", "Administrative-division code (行政区划代码) from the catalog record"),
        ("archive_code", "Archive code (档案馆代码) from the catalog record"),
        ("editor", "Editor(s) / issuing body"), ("publisher", "Publisher statement(s)"),
        ("call_number", "Princeton call number"), ("extent", "Physical extent of the book"),
        ("fonds_date_start", "Earliest year in the guide's extracted fonds"),
        ("fonds_date_end", "Latest year in the guide's extracted fonds"),
        ("volumes", "Number of volumes (wenshi indexes: locality volumes)"),
        ("dpul_url", "Original in DPUL"), ("catalog_url", "Princeton catalog record"), ("ark_url", "Persistent ARK")],
    "volumes": [
        ("id", "Volume ID (Figgy resource UUID)"), ("guide_id", "Guide it belongs to"),
        ("guide_title", "Title of that guide"), ("label", "Volume label (wenshi: locality, e.g. 成都市)"),
        ("province_zh", "Province"), ("place", "Matched prefecture/county, if any"),
        ("scans", "Scanned pages in this volume"), ("dpul_url", "Original in DPUL"),
        ("manifest_url", "IIIF manifest of the volume")],
    "fonds": [
        ("guide_title", "Handbook the fonds is listed in"), ("province_zh", "Province"),
        ("archive_code", "Archive code (档案馆代码)"), ("published", "Handbook publication year"),
        ("fonds_no", "Fonds number (全宗号) as printed"), ("name", "Fonds name (全宗名称)"),
        ("date_start", "First year of the records (cleaned)"), ("date_end", "Last year of the records (cleaned)"),
        ("counts", "Counts as printed (e.g. 目录数 / 案卷数), '; '-separated"),
        ("date_note", "What date cleaning changed, if anything"),
        ("scan", "Scan number within the guide"), ("viewer_url", "This scan in the DPUL viewer"),
        ("dpul_url", "The guide in DPUL"), ("guide_id", "Guide ID"), ("id", "Row ID")],
    "toc_entries": [
        ("guide_title", "Handbook"), ("province_zh", "Province"), ("published", "Handbook publication year"),
        ("section", "Section heading in the contents"), ("heading", "Entry (in fonds guides, usually a fonds name)"),
        ("page", "Page number printed in the book"), ("scan", "Scan number within the guide"),
        ("viewer_url", "This scan in the DPUL viewer"), ("dpul_url", "The guide in DPUL"),
        ("guide_id", "Guide ID"), ("id", "Row ID")],
    "wenshi_articles": [
        ("province_zh", "Province"), ("index_title", "Provincial index (e.g. …四川卷)"),
        ("locality", "Locality volume (e.g. 成都市)"), ("series", "Series title (e.g. …文史资料选辑)"),
        ("issue", "Issue number (第N辑)"), ("title", "Article title"), ("author", "Author(s)"),
        ("page", "Page in the indexed issue: the first number printed (empty where the index prints '( )')"),
        ("page_raw", "The printed page value when it is not a single plausible page, e.g. a range '116—120' or '期刊 4/94'"),
        ("scan", "Scan number within the index"), ("viewer_url", "This scan in the DPUL viewer"),
        ("dpul_url", "The index in DPUL"), ("volume_id", "Volume ID"), ("id", "Row ID")],
    "places": [
        ("id", "Place ID (level:code)"), ("level", "province, prefecture or county"),
        ("code", "Administrative-division code"), ("name_zh", "Name (Chinese)"), ("name_pinyin", "Name (pinyin)"),
        ("province_zh", "Province"), ("legacy_catalog_links", "Catalog records linked on the library's earlier map")],
}
TITLES = {"guides": "Guides", "volumes": "Volumes", "fonds": "Fonds (全宗)", "toc_entries": "Contents entries",
          "wenshi_articles": "Wenshi ziliao index entries", "places": "Places"}


def _join(v):
    return "; ".join(str(x) for x in v if x is not None) if isinstance(v, list) else v


def _csv_bytes(columns, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r.get(c) is None else _join(r.get(c)) for c in columns])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM: Excel then reads UTF-8


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def flat_tables(data: Path) -> dict[str, list[dict]]:
    """Joined, spreadsheet-friendly rows for each table."""
    t = {n: pq.read_table(data / f"{n}.parquet").to_pylist() for n in
         ("guides", "volumes", "fonds", "toc_entries", "wenshi_articles", "places")}
    pages = pq.read_table(data / "pages.parquet", columns=["guide_id", "page_n", "viewer_url"]).to_pylist()
    viewer = {(p["guide_id"], p["page_n"]): p["viewer_url"] for p in pages}
    g = {x["id"]: x for x in t["guides"]}
    vol = {x["id"]: x for x in t["volumes"]}

    def base(r):
        guide = g.get(r["guide_id"], {})
        return guide, {"guide_title": guide.get("title_zh"), "province_zh": guide.get("province_zh"),
                       "published": guide.get("year"), "dpul_url": guide.get("dpul_url"),
                       "scan": r.get("page_n"), "viewer_url": viewer.get((r["guide_id"], r.get("page_n")))}

    out = {"guides": [{**x, "type": x["kind"], "volumes": x["volumes_total"]} for x in t["guides"]],
           "volumes": [{**v, "guide_title": g[v["guide_id"]]["title_zh"], "province_zh": g[v["guide_id"]]["province_zh"],
                        "place": v["place_name_zh"], "scans": v["pages"], "dpul_url": g[v["guide_id"]]["dpul_url"]}
                       for v in t["volumes"]],
           "fonds": [{**f, **base(f)[1]} for f in t["fonds"]],
           "toc_entries": [{**e, **base(e)[1]} for e in t["toc_entries"]],
           "places": [{**p, "legacy_catalog_links": [l["url"] for l in p["legacy_links"] or []]} for p in t["places"]]}
    arts = []
    for a in t["wenshi_articles"]:
        guide, b = base(a)
        arts.append({**a, **b, "index_title": guide.get("title_zh"),
                     "locality": a.get("locality") or vol.get(a.get("volume_id"), {}).get("label")})
    out["wenshi_articles"] = arts
    return out


def dictionary_md(version: str) -> str:
    lines = [f"# Data dictionary (v{version})", "",
             "Flat CSV files, UTF-8 with BOM. Lists are joined with \"; \". Machine-extracted; see the README.", ""]
    for name, cols in COLUMNS.items():
        lines += [f"## {name}.csv ({TITLES[name]})", "", "| Column | Description |", "|---|---|"]
        lines += [f"| `{c}` | {d} |" for c, d in cols] + [""]
    return "\n".join(lines)


def build(release: Path, version: str, readme: str, license_text: str) -> list[dict]:
    data, out = release / "data", release / "downloads"
    (out / "csv" / "wenshi_articles").mkdir(parents=True, exist_ok=True)
    flat = flat_tables(data)
    manifest = []

    def add(path: Path, group: str, title: str, fmt: str, rows: int | None, note: str = ""):
        manifest.append({"group": group, "title": title, "file": str(path.relative_to(release)), "format": fmt,
                         "rows": rows, "bytes": path.stat().st_size, "sha256": _sha(path), "note": note})

    csv_files = {}
    for name in ("guides", "volumes", "fonds", "toc_entries", "places"):
        path = out / "csv" / f"{name}.csv"
        path.write_bytes(_csv_bytes([c for c, _ in COLUMNS[name]], flat[name]))
        csv_files[f"csv/{name}.csv"] = path
        add(path, "spreadsheet", TITLES[name], "CSV", len(flat[name]))

    arts_cols = [c for c, _ in COLUMNS["wenshi_articles"]]
    by_prov = {}
    for a in flat["wenshi_articles"]:
        by_prov.setdefault(a["province_zh"] or "unknown", []).append(a)
    for prov, rows in sorted(by_prov.items()):
        path = out / "csv" / "wenshi_articles" / f"{prov.replace('/', '_')}.csv"
        path.write_bytes(_csv_bytes(arts_cols, rows))
        csv_files[f"csv/wenshi_articles/{path.name}"] = path
        add(path, "wenshi_by_province", prov, "CSV", len(rows))
    path = out / "wenshi_articles.csv.gz"
    with gzip.open(path, "wb", compresslevel=9) as f:
        f.write(_csv_bytes(arts_cols, flat["wenshi_articles"]))
    add(path, "spreadsheet", f"{TITLES['wenshi_articles']} (all provinces)", "CSV, gzipped",
        len(flat["wenshi_articles"]), "Over 1M rows: too many for most spreadsheets; use the per-province files")

    path = out / "china-archive-guides-csv.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, src in csv_files.items():
            z.write(src, f"china-archive-guides-v{version}/{arc}")
        z.writestr(f"china-archive-guides-v{version}/DATA-DICTIONARY.md", dictionary_md(version))
        z.writestr(f"china-archive-guides-v{version}/README.md", readme)
        z.writestr(f"china-archive-guides-v{version}/LICENSE.txt", license_text)
    add(path, "everything", "All CSV files, data dictionary, README", "ZIP", None)

    db = out / "china-archive-guides.sqlite"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    for name in ("guides", "volumes", "pages", "fonds", "toc_entries", "wenshi_articles", "places"):
        cols = [c for c in pq.read_schema(data / f"{name}.parquet").names if c != "html"]
        tbl = pq.read_table(data / f"{name}.parquet", columns=cols)
        con.execute(f'CREATE TABLE {name} ({", ".join(f"[{c}]" for c in cols)})')
        con.executemany(f'INSERT INTO {name} VALUES ({", ".join("?" * len(cols))})',
                        ([json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in row.values()]
                         for row in tbl.to_pylist()))
    con.executescript("""
        CREATE INDEX fonds_guide ON fonds(guide_id); CREATE INDEX toc_guide ON toc_entries(guide_id);
        CREATE INDEX art_volume ON wenshi_articles(volume_id); CREATE INDEX pages_guide ON pages(guide_id, page_n);
        CREATE VIEW fonds_flat AS SELECT g.title_zh AS guide_title, g.province_zh, g.year AS published, f.fonds_no, f.name,
          f.date_start, f.date_end, f.counts, p.viewer_url, g.dpul_url, f.guide_id, f.page_n
          FROM fonds f JOIN guides g ON g.id = f.guide_id LEFT JOIN pages p ON p.guide_id = f.guide_id AND p.page_n = f.page_n;
        CREATE VIEW wenshi_flat AS SELECT g.province_zh, g.title_zh AS index_title, a.locality, a.series, a.issue, a.title,
          a.author, a.page, p.viewer_url, a.volume_id, a.page_n
          FROM wenshi_articles a JOIN guides g ON g.id = a.guide_id LEFT JOIN pages p ON p.guide_id = a.guide_id AND p.page_n = a.page_n;
    """)
    con.commit()
    con.execute("VACUUM")
    con.close()
    path = out / "china-archive-guides.sqlite.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(db, f"china-archive-guides-v{version}.sqlite")
    db.unlink()
    add(path, "everything", "SQLite database: all tables (page text, no HTML) + joined views", "SQLite, zipped", None,
        "Open with DB Browser for SQLite, R, Python, or any SQLite client")

    (out / "manifest.json").write_text(json.dumps({"version": version, "files": manifest}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return manifest
