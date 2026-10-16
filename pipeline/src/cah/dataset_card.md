---
pretty_name: Chinese Archival Handbooks and Wenshi Ziliao Indexes (Princeton East Asian Library)
license: cc0-1.0
language:
  - zh
  - en
tags:
  - archives
  - finding-aids
  - china
  - local-history
  - wenshi-ziliao
  - digital-humanities
  - ocr
  - iiif
size_categories:
  - 1M<n<10M
configs:
  - config_name: guides
    default: true
    data_files: data/guides.parquet
  - config_name: volumes
    data_files: data/volumes.parquet
  - config_name: pages
    data_files: data/pages.parquet
  - config_name: fonds
    data_files: data/fonds.parquet
  - config_name: toc_entries
    data_files: data/toc_entries.parquet
  - config_name: wenshi_articles
    data_files: data/wenshi_articles.parquet
  - config_name: places
    data_files: data/places.parquet
---

# Chinese Archival Handbooks and Wenshi Ziliao Indexes

Machine-readable tables of contents, fonds lists and index entries from two collections at Princeton
University Library's East Asian Library, digitized in the library's
[DPUL "East Asian Library Tables of Contents" exhibit](https://dpul.princeton.edu/ealtoc):

- **Chinese archival handbooks (档案馆指南)**: guides published by Chinese archives describing their
  holdings. Princeton holds the largest known collection. The scans cover each handbook's **table of
  contents and fonds list (全宗介绍 / 全宗名册)**, not the whole book.
- **Wenshi ziliao (文史资料) indexes**: the provincial volumes of *中国文史资料总目录电子版*, a general
  index of the "literary and historical materials" series published by local committees of the Chinese
  People's Political Consultative Conference: memoirs and local history, by locality, series and issue.

The dataset is built for **discovery**: finding which archives hold records on a subject, period or place,
and which local-history volumes contain relevant articles. Every row links back to the original page image
in DPUL, which remains the authoritative source.

**Version $version ($date).** Text and structured fields are machine-extracted and have not been
reviewed by people. See [Limitations](#limitations).

## Contents

| Config | Rows | One row per |
|---|---|---|
| `guides` (default) | $n_guides | Handbook ($n_handbooks) or wenshi ziliao provincial index ($n_wenshi): catalog metadata, province, links |
| `volumes` | $n_volumes | Volume: each handbook is one; wenshi indexes have $n_wenshi_volumes locality volumes |
| `pages` | $n_pages | Scanned page: HTML and plain-text transcription, page type, OCR engine, IIIF image service, DPUL viewer link |
| `fonds` | $n_fonds | Fonds (全宗) row from a handbook's fonds register: number, name, counts, date range |
| `toc_entries` | $n_toc | Table-of-contents entry in a handbook (in fonds guides, mostly fonds names) |
| `wenshi_articles` | $n_articles | Wenshi ziliao index entry: series, issue (辑), title, author, page |
| `places` | $n_places | Place (province, prefecture, county) from the library's earlier map of the collection, with its catalog links |

The collections cover $n_provinces provinces and national-level archives; handbooks were published between
$year_min and $year_max.

**Not included:** page images (use the IIIF `image_service` or `viewer_url` in `pages`); full texts of
wenshi ziliao articles (only the index entries); the full text of the handbooks (only the scanned contents
and fonds lists).

### Keys

- `guides.id`: the DPUL identifier (`https://dpul.princeton.edu/ealtoc/catalog/{id}`).
- `volumes.id`: the Figgy (Princeton's repository) resource UUID of the volume's IIIF manifest.
- `pages`: `(guide_id, page_n)` is unique. `page_n` numbers scans across a guide's volumes and matches the
  site's `#p{n}` anchors. `canvas_id` is the IIIF canvas URI.
- `fonds`, `toc_entries` and `wenshi_articles` rows point to their page by `guide_id` + `page_n` (and
  `canvas_id`); `wenshi_articles.volume_id` gives the locality volume.

### Field notes

- `pages.html`: sanitized transcription using a small set of tags (`h2-h4`, `p`, `table` with
  `rowspan`/`colspan`, `ul class="toc"` with `span class="au"` for authors and `span class="pg"` for
  page numbers). `pages.text` is plain text derived from it.
- `pages.page_type`: `toc`, `fonds_table`, `holdings_table`, `wenshi_index`, `front_matter`, `prose`,
  `other`, or empty for pages read by the fallback OCR engine.
- `fonds.date_start` / `date_end` are cleaned: Republic of China years (民国1-38) are converted to
  1912-1949, reversed ranges are swapped, and years outside 1368-2030 are dropped. The values as extracted
  are kept in `date_start_raw` / `date_end_raw`, with a `date_note` saying what changed.
- `wenshi_articles.page` is the page number printed in the index (the page in the indexed volume, not a
  scan number). It is empty where the index prints "( )" or no number.
- `extraction` is `vlm` (vision-language model) or `rules` (rule-based parsing of fallback OCR).

## Loading

```python
from datasets import load_dataset

guides = load_dataset("$repo", "guides", split="train")
articles = load_dataset("$repo", "wenshi_articles", split="train")
```

Or query the Parquet files directly, e.g. with DuckDB:

```sql
SELECT g.title_zh, f.fonds_no, f.name, f.date_start, f.date_end
FROM 'hf://datasets/$repo/data/fonds.parquet' f
JOIN 'hf://datasets/$repo/data/guides.parquet' g ON g.id = f.guide_id
WHERE f.name LIKE '%粮食局%';
```

## Downloads without code

The `downloads/` folder has the same data for spreadsheet and SQL users:

- **Flat CSV files** (`downloads/csv/`): guides, volumes, fonds, contents entries and places, with the guide
  title, province and DPUL links on every row, so no joins are needed. Files are UTF-8 with a byte-order
  mark (Excel shows Chinese correctly).
- **Wenshi ziliao index entries, one CSV per province** (`downloads/csv/wenshi_articles/`). All of them
  together (over 1M rows) are in `downloads/wenshi_articles.csv.gz`.
- **Everything in one file**: `downloads/china-archive-guides-csv.zip` (all CSVs, data dictionary, README,
  license) and `downloads/china-archive-guides.sqlite.zip` (an SQLite database with all tables (page text,
  not HTML) and joined views `fonds_flat` and `wenshi_flat`).
- `downloads/manifest.json` lists every file with row counts and SHA-256 checksums.

## How it was made

1. **Harvest.** Catalog metadata and IIIF manifests were harvested from DPUL and Figgy item by item
   (Figgy has no bulk export), with incremental change detection. Collections: DPUL browse categories
   "Chinese Archival Handbooks" and "Wenshi Ziliao".
2. **Transcription.** Page images were requested at 1,500 px width from Princeton's IIIF Image API.
   $pct_dashscope of pages were transcribed by the vision-language model **Qwen3-VL-Plus** (Alibaba Cloud
   DashScope, `qwen3-vl-plus`). Handbooks were asked for HTML plus structured fonds and contents rows
   (prompt v2). Wenshi index pages were asked for HTML only (prompt `wenshi-v3`); their entries are parsed
   from the HTML. $n_rapidocr pages were read instead by **RapidOCR** (PaddleOCR models, run locally)
   with rule-based extraction: the pages the model service refused (see below). Eight pages on which the
   model failed repeatedly (very wide, mostly empty tables; one very dense index page) were rescued with
   targeted requests: fonds rows only, or the page split into two halves. `pages.ocr_notes` records
   both cases.
3. **Normalization.** HTML was sanitized to an allow-list of tags; fonds dates were cleaned as above;
   catalog dates with MARC placeholder digits (e.g. `2004-699`) were normalized to the first year.
4. **Places.** Each guide has a province from its administrative-division code (行政区划代码) in the
   catalog record, or from its matched place. Handbooks were matched to prefecture and county places from
   the library's earlier ArcGIS map by catalog record ID, then admin code, then place name. Wenshi volumes
   were matched by locality name within their province.

The pipeline is open source: https://github.com/PULdischo/china-archive-guides

## Limitations

- **Machine extraction, not reviewed.** Transcriptions and structured rows can contain recognition errors,
  missed or merged entries, and misread numbers. Check the page image before relying on a detail. In a
  40-page test on wenshi index pages, entries parsed from the model's HTML matched counts from an
  independent OCR engine on most pages, and every entry was correct on a page checked by hand. There is no
  hand-keyed gold standard yet.
- **Content moderation.** The model service refused $n_blocked pages ($pct_blocked) as "inappropriate
  content", unpredictably and often on politically sensitive material (e.g. a fonds list including
  materials on 1989). These pages were read by the fallback OCR engine, so they are present but with
  plainer layout and rule-based (less complete) structured rows; `pages.ocr_notes` records this. A
  dataset built only from the model's output would silently under-represent sensitive material; this one
  does not drop it.
- **Scope of the scans.** Handbook scans contain contents and fonds lists only; descriptions of fonds in
  the body of the books are not included.
- **Index counts.** The wenshi indexes yield about 23 entries per page ($n_articles in total), more than
  the roughly 500,000 articles often cited for this series. Entries are not deduplicated across volumes
  (reprinted articles appear more than once); about 0.7% are exact repeats within a volume.
- **Restricted volumes.** Volumes restricted in DPUL were skipped: $restricted.
- **Places.** Matching to the earlier map's places is incomplete for townships and districts, and
  historical place names (e.g. 地区 later renamed 市) are not yet reconciled.
- **Dates.** Wenshi index items carry the CD-ROM publication year (2009-2012), not the years of the indexed
  series.

## Rights and license

This dataset (structured data and transcriptions of contents, fonds-list and index pages) is released
under **CC0 1.0**. It consists of bibliographic and descriptive information extracted for discovery; it
does not include page images or the full texts of indexed works.

The source scans are made available by Princeton University Library through DPUL, which carries their own
rights statements: the archival handbooks are marked *No Known Copyright* and the wenshi ziliao CD-ROM
indexes *Copyright Not Evaluated*. No rights statement appears on the scanned index pages or in their
catalog records. Please link to DPUL when presenting material from the dataset.

## Maps

Province boundaries used by the project's website come from geoBoundaries (CHN ADM1, public domain, via
Wikimedia Commons). The website shows only provinces that have guides in the collection and draws no
national outline. geoBoundaries' Xinjiang boundary includes Aksai Chin, which is also claimed by India.

## Citation

```bibtex
@dataset{princeton_china_archive_guides_$version,
  title     = {Chinese Archival Handbooks and Wenshi Ziliao Indexes (Princeton East Asian Library)},
  author    = {{Princeton University Library}},
  year      = {2026},
  version   = {$version},
  publisher = {Hugging Face},
  url       = {https://huggingface.co/datasets/$repo},
  note      = {Machine-extracted from scans in the Princeton University Digital Library (DPUL)}
}
```

## Changelog

$changelog
