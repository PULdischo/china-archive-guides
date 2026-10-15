# China Archive & Local History Guides — Project Plan

*Draft v0.7 — 2026-10-11*

## 1. Goal

Make Princeton's Chinese archival handbooks (档案馆指南) and wenshi ziliao (文史资料) indexes searchable together, with full-text, metadata, and spatial search. Publish the underlying data as an open dataset on Hugging Face and with a citable DOI. Every record links back to the original DPUL item, catalog record, and IIIF page. Over time, extend coverage to guides held by peer libraries.

### Design principles
- **Link back, always.** Figgy/DPUL remain the system of record for images and descriptive metadata.
- **Contribute back when possible.** Derived text and metadata are shaped so that DPUL/Figgy and the catalog can ingest them later (§3.6).
- **Static first.** Deploy on GitHub Pages with no servers to run. The dataset must remain useful even if the site goes away.
- **Incremental and repeatable.** The pipeline runs on a schedule, processes only what changed, and never overwrites human corrections (§3.7).

### Primary users (initial)
1. **A graduate student planning a dissertation.** "Which county/prefectural archives hold fonds on X (e.g., 1950s grain procurement, a specific factory, missionary schools) in region Y, and what period do they cover?"
2. **A faculty member planning an archival trip for a new project.** "Within a feasible itinerary (a province, or a rail corridor), which archives look richest for my topic? How current is the information, and what else should I read before going?"

## 2. Source inventory (what exists today)

Confirmed by probing the live systems on 2026-10-11.

| Source | What it is | Size | Access |
|---|---|---|---|
| [LibGuide](https://libguides.princeton.edu/ealdbs/chinese#s-lg-box-23258703) | Entry point, with context text | — | HTML |
| DPUL [Chinese Archival Handbooks](https://dpul.princeton.edu/ealtoc/browse/chinese-archival-handbooks) | Scans of each handbook's **table of contents and fonds list (全宗介绍)**. These are not full books: e.g., Hunan guide = 28 scanned pages of a 450-page book | **343 items** | Blacklight JSON (`/ealtoc/catalog/{id}.json`); IIIF Presentation v2 manifests on Figgy; IIIF Image API; Figgy JSON-LD; MARCXML via catalog |
| DPUL [Wenshi ziliao](https://dpul.princeton.edu/ealtoc/browse/wenshi-ziliao-literary-and-historical-materials) | 中国文史资料总目录电子版: provincial CD-ROM indexes, rendered as scans. Each item is a **multi-volume IIIF collection** with one sub-manifest per locality (e.g., Guizhou → 10) | **29 items**; per LibGuide, ~1,250 documents, ~45,800 pages, ~500k article entries | Same as above |
| [ArcGIS map](https://pumagic.maps.arcgis.com/apps/instant/sidebar/index.html?appid=91da55022dff4d2dba991770028c8a5a) (**we maintain; to be retired**) | Instant App → webmap `e8e87e64…` → 4 public FeatureServer polygon layers (National 1, Provincial 18, Prefectural 102, County 132) | **253 places** (vs. 343 handbooks) | ArcGIS REST `query?f=geojson` |

**Figgy does not offer bulk export.** We harvest item by item through the public JSON and IIIF endpoints.

### Useful details found
- **DPUL JSON fields:** bilingual title, editor, publisher, date, call number (`CD2055 .Z443001 1996`), extent, `spatial` (LCNAF place), subjects, ARK, catalog link, rights. The record also has the **manifest URL** (`content_metadata_iiif_manifest_field_ssi`) and change timestamps (`system_updated_at_dtsi`, `timestamp`). Figgy JSON-LD likewise has `system_updated_at`, and manifests return an **ETag**. These support incremental harvesting.
- **Codes in `description`:** handbooks carry `行政区划代码` (admin division code) and `档案馆代码` (archive code, e.g. `443001`). The call number also embeds the archive code. These are the join keys to the map layers, which use the same GB/T 2260 codes (`Provinc_Co`, `Prefect_Co`, `County_Cod`).
- **Map → catalog:** map features link to `catalog.princeton.edu` MMS IDs. The DPUL record's `view-in-catalog` holds the same MMS ID, which makes it the second join key.
- **Map schema is "wide":** `Title_1..4 / Year_1..4 / URL_1..4` per place. The layers were last edited 2024-02-08. Wenshi ziliao does not appear on the map at all.
- **Data quality:** some dates are malformed (`2004-699`, `2011-1299`). These come from MARC `u` placeholders and need normalization.
- **No OCR/text layer** in the manifests. Canvases expose original JPEG downloads and full-resolution IIIF.
- **Timestamps alone can't detect changes.** `system_updated_at` changes on reindexing: e.g., a 2021 item shows an update on 2026-07-11. Use content hashes to decide what to reprocess.

## 3. Architecture

```
                 ┌──────────── scheduled (GitHub Actions cron) ────────────┐
 DPUL JSON ─┐    │                                                         │
 IIIF/Figgy ├─► harvest ─► diff ─► fetch new images ─► OCR ─► extract ─► reconcile ─► apply corrections ─► release
 MARCXML   ─┤    (raw, hashed)        (cache, not in git)   (cache keyed by input hash + model/prompt version) │
 ArcGIS    ─┘                                                                                               │
                                       ┌───────────────────────────┬──────────────────────┬────────────────┘
                                       ▼                           ▼                      ▼
                               HF dataset (Parquet)       Static site build        DPUL/Figgy contribution
                               + DataSpace/Zenodo DOI     (11ty + Pagefind)        package (annotations, CSV)
                                                          → GitHub Pages
```

### 3.1 Harvest (Phase 1)
- Page DPUL browse/search JSON to enumerate items. For each item, pull the item JSON, the IIIF manifest (recursing into collection sub-manifests for wenshi ziliao), Figgy JSON-LD, and MARCXML.
- Import the 4 ArcGIS layers once as **seed and validation data** (reproject from EPSG:3857 to 4326). Melt the wide `Title_n` columns into a long table `(place_code, mms_id, title, year)`. See §3.8 for retiring the map.
- Download images through the IIIF Image API at a fixed OCR resolution. Throttle politely, set an identifying User-Agent, and tell the Figgy team about the crawl schedule.
- Keep raw responses as immutable snapshots, so reprocessing never requires re-harvesting. The image cache lives on research storage, not in git.

### 3.2 Text extraction (Phase 2)
- **Material:** printed, mostly simplified Chinese with some traditional. Pages are heavily **tabular**: fonds lists with number, name, date range, and volume count (卷数). TOCs have leader dots and page numbers. Wenshi ziliao index pages hold dense article lists.
- **Approach (decided 2026-10-11):** **DashScope (Qwen VL, `qwen3-vl-plus`)** is the primary engine. One call per page returns the transcription plus fonds/contents/article rows as JSON. **RapidOCR** (PaddleOCR models, local CPU) with rule-based extraction is kept as a baseline. Both are cached per engine, so they can be compared page by page (`cah compare`). Still benchmark against a hand-keyed **gold set (~50 pages, stratified by source type, decade, and layout)** and measure character error rate (CER) and per-field accuracy. Re-check model and prompt choices (e.g. `qwen-vl-ocr`, larger Qwen VL models) against it.
- **Cost tracking:** every DashScope response's token usage is cached with the result. The smoke test measured ~4.2k tokens per dense page (~2.5k of them image tokens), which gives the basis for a full-collection estimate.
- **Output:** a page-level **HTML transcription** (tables with their merged cells, headings, contents lists) is the canonical page representation. It is sanitized to an allowlist of structural tags (`cah.markup`); plain text is derived from it for search and the dataset. Structured rows (fonds, contents entries, articles) are extracted alongside it only where they apply. Pages with other tables (e.g. holdings of reference materials 资料) stay HTML-only. Region coordinates (`xywh`) for highlighting hits on the image are a later step. The same output is the contribution format for Figgy (§3.6).

### 3.3 Structured extraction (Phase 2–3)

| Entity | Key fields | Source |
|---|---|---|
| **Guide** (publication) | ark, figgy_id, mms_id, titles (zh/pinyin/en), editor, publisher, year, edition, call no., rights, DPUL/IIIF/catalog URLs | DPUL + MARC |
| **Page** (canvas) | figgy canvas id, guide/volume, sequence, label, image service URL, page type, HTML transcription, plain text, OCR model/prompt version | IIIF + OCR |
| **Repository** (archive) | 档案馆代码, names (zh/pinyin/en), level (national/prov/pref/county/other), admin code, seat coordinates, status/successor, Wikidata QID | Call no., MARC, map, guide text |
| **Place** | GB/T 2260 code, names, level, parent, geometry ref, **valid-from/to** | ArcGIS seed + licensed boundaries + code-change tables |
| **Fonds (全宗)** | fonds no., name, creator, date range, extent (卷/件), description, canvas + region | OCR of fonds lists |
| **TOC entry** | heading, level, page, canvas + region | OCR of TOCs |
| **Wenshi article** | title, author, series/issue (辑), year, place, page, canvas + region | OCR of index pages |

**IDs.** Primary keys reuse Figgy resource and canvas UUIDs, ARKs, and MMS IDs wherever they exist. Minted IDs (fonds, articles, repositories) are deterministic, derived from source canvas + position or from official codes, so they stay stable across reruns.

**Provenance.** Every machine-derived field carries the model/version, prompt version, confidence, and source region. Human-corrected fields carry `corrected_by`/`corrected_at`.

### 3.4 Static site stack (recommended)

| Layer | Choice | Why |
|---|---|---|
| Site generator | **11ty (Eleventy)** with **Nunjucks** templates | Simple, stable, fast builds; data files + pagination generate one page per guide/repository/place/volume; official i18n plugin for `/zh/` and `/en/` routes; no client JS unless we add it |
| Search | **Pagefind** (extended build) | Static, index loaded in chunks, filters/sort/sub-results; see §3.5 |
| Map | **MapLibre GL JS** + **PMTiles** for boundary layers | Vector tiles from one static file over HTTP range requests; falls back to simplified TopoJSON per level if Pages range support proves unreliable |
| Basemap | OpenFreeMap or a Protomaps extract | No API key; check that it loads acceptably from mainland China (see §5) |
| Page viewer | Link to the Figgy/DPUL viewer; optional embedded **OpenSeadragon** (IIIF `info.json`) with an overlay for highlighted regions | Plain JS, no framework; keeps the originals as the destination |
| Client JS | Vanilla ES modules, one per widget (`search.js`, `map.js`, `viewer.js`), included only on the pages that use them; bundled with esbuild if needed | Only three interactive widgets; nothing else needs a framework |
| Styling | Plain CSS with design tokens (or Tailwind if the team prefers) | Low maintenance |
| Build/deploy | **GitHub Actions** → `actions/deploy-pages` | Same CI that runs the pipeline |
| Pipeline | **Python 3.12 + uv**, httpx, pydantic (schemas), **DuckDB** (joins, Parquet), OpenCC (simplified ⇄ traditional), pypinyin, geopandas/shapely | Reproducible and testable; Parquet is the canonical store feeding both HF and the site |

**Pipeline → site handoff.** The pipeline exports site-ready JSON (one file per entity, plus per-guide/per-volume files where large) into `site/_data/` or a build directory. 11ty reads plain JSON, so the Node side needs no Parquet/DuckDB dependency. Data is validated in the pipeline (pydantic). The pipeline also exports JSON Schemas, and a small pre-build check validates the site's input against them.

**Search index build.** A post-build Node script uses the Pagefind Node API (`addCustomRecord`) to index canvas-level records from the exported JSON, with filters, sort keys and simplified/traditional/pinyin variants. Results link to the canvas anchors on guide/volume pages (`/guides/{id}/#p{seq}`). The site's HTML is indexed for guide, repository and place pages.

**Page types** (all statically generated): guide, repository, place (province → prefecture → county), wenshi volume, search, map, about/methodology, dataset/downloads.

**GitHub Pages limits to design around:** 1 GB published site, ~100 GB/month soft bandwidth, 10 builds/hour, and 100 MB per file in git. Every record and all derived data must fit in that budget, which rules out one HTML page per fonds or per article (§3.5).

### 3.5 Pagefind assessment

**Verdict: suitable for the MVP, including most "spatial" search, subject to a bake-off at the end of Phase 2.**

What fits:
- **CJK.** The extended release (the default for `npx pagefind`) segments Chinese at index time. Since **v1.5** it also segments unspaced Chinese queries. Older versions required spaces in queries, so pin ≥ 1.5.
- **Faceted metadata.** Multi-valued `filters` cover source type, province/prefecture/county code, archive level, publication decade, fonds-coverage decades, and script. The filter API supports `any`/`all`/`none`/`not`, and `sort` covers publication year and title.
- **Granularity.** **Sub-results** link to anchors within a page. One indexed record per scanned page (canvas) can surface individual fonds or article rows as anchored sub-results.
- **Building the index.** The Node API (`addCustomRecord`) builds the index straight from Parquet-derived records, independent of how the HTML looks.
- **Spatial without a server.** Admin-unit search = filter on place codes. Draw-a-region or radius search = compute the matching place/repository codes in the browser (turf.js over ~3k repository points / simplified polygons), then pass them as an `any` filter. So spatial search largely works on Pagefind.

Known gaps and mitigations:

| Gap | Mitigation |
|---|---|
| Dictionary segmentation can split institution names and proper nouns oddly, and OCR errors break words | Test on gold queries; append key entity names (fonds/repository names) as extra unsegmented terms in record content; fall back as described below if recall is poor |
| No simplified ⇄ traditional folding | Add OpenCC-converted variants of titles and fonds names to the indexed content; check that excerpts stay readable |
| No pinyin/English matching | Add pinyin (pypinyin, plus catalog romanization) and English titles to records. Machine translation of fonds names is optional and flagged |
| No numeric range filters (e.g., "covers 1958–62") | Use decade (or 5-year) bucket filters, plus exact client-side refinement from record `meta` |
| Per-language index splitting would separate zh and en records | Index all records as `zh` (or force the language) so English/pinyin queries reach the same index; the UI language is separate |
| Scale/file count: 500k article-level records would overload Pages | Index at the **canvas level** (~10k handbook pages + ~46k wenshi pages ≈ 56k records) plus guide/repository/place pages; rows become sub-results |
| No fuzzy matching; phrase-query behavior unverified | Include in the bake-off |

**Result (2026-10-11, full handbook set: 345 guides, 6,192 pages): keep Pagefind, but with our own tokens.**

- **Root cause:** since 1.5.0, Pagefind segments CJK *queries* in the browser with `Intl.Segmenter` (ICU), but segments the *index* at build time with its own Rust segmenter. The word boundaries differ. `四川省粮食` returned 0 guides (四川省 is almost absent as an index token). Unspaced names matched only on their first word (`交通部公路总局` = `交通部`, 298 guides). `郵政` matched 216 guides via 郵 alone, vs 86 for `邮政`.
- **Fix (`cah search` + `site/src/assets/search-core.js`):**
  - Text is folded to simplified (a 3,751-pair character map from OpenCC), spaces between Han characters are dropped, and each Han run is indexed as overlapping unigrams + bigrams.
  - Han tokens are encoded as opaque ASCII words (四川 → `h22235x24029`), so Pagefind's segmenter and stemmer can't re-split them. The browser applies identical normalization and encoding to queries (Python/JS parity tested: 204/204 texts identical).
  - The exact string is verified on candidate pages, with whole-word matching for romanized terms.
  - One record per scanned page carries a `guide` filter. One search therefore returns per-guide match counts without loading fragments. Guides are ranked by number of matching pages, and only the top guides' best pages are loaded.
- **Scores** on the auto-generated test set (82 queries; ground truth = guides containing the query string; `data/export/search/eval.json`, run with `npm run eval:search`):

  | Query type | n | Pagefind default (recall@10 / prec@10) | Custom tokens |
  |---|---|---|---|
  | Fonds names | 25 | 0.60 / 0.18 | 1.00 / 1.00 |
  | Topics | 30 | 0.89 / 0.90 | 1.00 / 1.00 |
  | Fonds names, traditional | 10 | 0.50 / 0.15 | 1.00 / 1.00 |
  | Topics, traditional | 7 | 0.71 / 0.71 | 1.00 / 1.00 |
  | Romanized titles | 10 | 0.97 / 0.97 | 0.98 / 0.98 |
  | **All** | 82 | **0.75 / 0.58** | **1.00 / 1.00** |

  Mean latency was ~200 ms per query on a local server (topics ~370 ms). Index: 37 MB (the whole site is 47 MB, down from 59 MB). Precision is high partly by construction (the check is exact-string). The test set does not yet judge *ranking* among many relevant guides (e.g. 268 guides contain 粮食局). Add real researcher queries with graded relevance.
- **SQLite fallback not needed.** Still open: whether ranking by matching-page count suits researchers, phrase ranking for multi-word romanized queries, and the cost of Pagefind's own excerpts (unused; we render excerpts from `meta.text`).

#### Search performance at full scale (measured 2026-10-15; 53,000 records)

**Why broad queries are slow:** Pagefind's cost grows with the number of matching *records*, not with bytes
downloaded. It scores and builds a result for every match, at ~0.3 ms each. Timings are warm, local
server:

| Query | Matching pages | Main search | Notes |
|---|---|---|---|
| 学校 | 6,152 | ~2.0 s | + ~1.0 s for the top-10 restricted search |
| 中共 | 4,754 | ~1.55 s | + ~0.8 s |
| 土地改革 | 786 | ~0.3–0.6 s | |
| 交通部公路总局 | 63 | ~30–60 ms | |

Downloads are only 30–200 KB per query. Experimental indexes built from the same records:

| Variant | Size | 学校 | 中共 | Finding |
|---|---|---|---|---|
| Current (all, per-guide filter) | 352 MB | 2.0 s | 1.55 s | baseline |
| No per-guide filter | 351 MB | 1.7 s | 1.3 s | filter bookkeeping is only ~15% |
| Bigrams only (no unigrams) | 304 MB | 2.1 s | 1.5 s | no gain |
| Handbooks only (6.5k records) | 37 MB | 0.10 s | 0.19 s | archives alone are fast |
| Wenshi only (46.5k records) | 317 MB | 1.4 s | 0.8 s | still slow: 88% of matches are wenshi pages |
| Fonds rows only (55k short records) | 13 MB | <0.01 s | 0.01 s | instant, but fonds names alone miss contents-only listings |

Filtering a shared index does not substitute for separate indexes. A filtered search still scores every
match first: 学校 restricted to handbooks takes 1.06 s, vs 0.10 s in a handbooks-only index.

**Custom index estimate:** 10.5M (bigram, page) postings, ~23 MB as delta-varints (vs Pagefind's 352 MB).
The postings for 学校 are ~14 KB, and intersecting them in the browser takes milliseconds regardless of how
common the term is.

**Options**

1. **Split the Pagefind index into archives and local histories, and search both in parallel.** Results
   render progressively: archives in <0.2 s; local histories still 1–3 s for very common terms. Low effort.
2. **Skip the second search** with a precomputed fragment → unit map, ranking from the main results.
   Saves ~1 s on common terms; relies on Pagefind internals. Low effort.
3. **Custom static inverted index (replace Pagefind).** Postings sharded by bigram (~6 KB shards),
   unit metadata (1,591 units) loaded once, page texts in shards for verification and excerpts.
   - Expected under ~0.3 s for any query, including the most common terms.
   - Facet counts become true guide/volume counts (Pagefind's are page counts).
   - Site shrinks by ~300 MB.
   - We already own tokenization, exact-string verification and ranking; Pagefind only stores postings
     and fragments. Medium effort, more code to own.
4. **Entry-level archive results:** fonds rows and contents entries (~115k) as first-class results,
   showing "fonds X, dates, in archive Y". This is the most direct answer to a researcher's question.
   Complements 1 or 3.

**Recommended:** prototype option 3 and benchmark it with the existing harness (`eval-search.mjs`,
Node + browser) before switching. Present results in two sections, **Archives** and **Local histories**
(the separation, done in the interface rather than the index), with option 4's fonds and contents
entries in the Archives section. Keep Pagefind until the prototype beats it on the eval set.

**Bake-off (Phase 2 exit):**
- **Queries:** 40–60 real queries collected from grad students, faculty, and reference staff, in Chinese simplified/traditional, pinyin, and English.
- **Measures:** recall@10 against judged results; index size and file count; first-query latency on a throttled connection; and results from mainland China.
- **Fallbacks, if Pagefind fails on recall,** in order of how well they keep a static deploy:
  1. SQLite FTS5 over **pre-tokenized overlapping bigrams**, served statically via `sql.js-httpvfs` with HTTP range requests. Fully static, with true range queries.
  2. Hosted Meilisearch/Typesense. Needs a server, so only if the library will host it.

### 3.6 Contributing back to DPUL/Figgy
Figgy has no bulk export, and its ingest path for third-party enrichment is unknown, so design outputs to be ingestible and ask early:
- **Text:** per-canvas W3C Web Annotations (IIIF Presentation 3 AnnotationPages, `motivation: supplementing`) whose body is the sanitized HTML transcription (`format: text/html`, `language: zh`) and a plain-text alternative, keyed by Figgy canvas IDs. Also offer hOCR/ALTO if Figgy's OCR features prefer it.
- **Metadata enhancements:** CSV/JSON keyed by Figgy UUID + ARK + MMS ID, one row per field change, with provenance. Covers normalized dates, place codes, repository IDs, and structured TOCs as IIIF `structures`/ranges.
- **Catalog error reports:** a periodic report of malformed dates and missing codes for cataloging staff.
- **Licensing:** keep contributions CC0 and machine-derived fields clearly flagged, so DPUL can accept them selectively.

### 3.7 Incremental pipeline & scheduling
- **Change detection:** enumerate DPUL items, then compare `system_updated_at`, manifest ETag, and a **content hash** of the normalized manifest and metadata against the last snapshot. Only new or changed canvases go downstream. Deleted items become **tombstones**: kept in the dataset with `status: withdrawn`, never silently dropped.
- **Caching:** each stage caches on `(input hash, code version, model/prompt version)`. Changing the OCR model or prompt re-runs only the affected stage.
- **Human corrections are an overlay:** they live as reviewed files in git (`corrections/*.csv`, keyed by stable ID + field) and are applied after extraction. Reprocessing never overwrites them, and conflicts are reported.
- **Schedule:**
  - **Weekly**: harvest + diff (GitHub Actions cron).
  - **When a diff is non-empty**: run OCR/extraction. Use GitHub Actions if OCR goes through an API; use Princeton research computing (or a self-hosted runner) if OCR runs locally on GPUs.
  - **Monthly (or on demand)**: dataset release + site rebuild.
  - Every run publishes a run report: items added/changed/removed, OCR cost, and validation failures.
- **Validation gates:** schema checks (pydantic), referential integrity (every fonds → page → guide), and code validity. A failed gate blocks the release.
- **Versioning:** semantic dataset versions with a changelog of added/changed/removed records by ID.

### 3.8 Retiring the ArcGIS map
We maintain the map, so retirement is in our hands:
1. Import its layers as seed data and produce a **reconciliation report** in both directions: places on the map with no matched guide, and guides with no map place.
2. Reach **parity**: every place and link on the old map appears on the new map view.
3. Update the LibGuide links. Replace the Instant App with a notice and link, and leave it up for one term before unpublishing.
4. Archive the final layer export in the dataset's `provenance/` folder.

**Boundary geometry license:** before redistributing polygons on HF, confirm the license of the polygons used in the ArcGIS layers. Otherwise rebuild from an openly licensed source, e.g. geoBoundaries (CC BY 4.0), after checking China ADM3 coverage. GADM is non-commercial, so avoid it. The dataset can always ship codes + seat coordinates (points) even if polygons can't be redistributed.

### 3.9 Dataset publication
- **Hugging Face dataset** (org/name TBD), with one config per entity: `guides`, `pages`, `repositories`, `places`, `fonds`, `toc_entries`, `wenshi_articles`. Parquet files.
- **Dataset card** covers provenance, the CC0 license for derived metadata and text, machine-generated flags and quality metrics (CER), known gaps, and citation.
- **Images are not embedded;** records carry IIIF image/manifest URLs and DPUL links.
- **Releases** are pushed from CI with `huggingface_hub` and tagged with the dataset version (HF revisions), so users can pin versions.
- **Citable DOI:** a GitHub → Zenodo release, and/or Princeton DataSpace, per major version.
- **Also** ship CSV/JSONL and GeoJSON on the site's downloads page for non-ML users.

## 4. User needs → features

| Need | Feature | Notes |
|---|---|---|
| Find archives with material on a topic | Full-text over fonds names + TOCs, with variant expansion (simplified/traditional, pinyin) | Core value is **fonds-level** search |
| Narrow to a period | Fonds coverage-decade filter + exact refinement | Requires parsing ranges like 1949—1985 |
| Plan a route / itinerary | Map with result density by place; draw/radius selection; export a shortlist (CSV/PDF) | "Trip list" kept in the browser (localStorage), with citations |
| Judge whether a guide is current | Guide publication year shown prominently; flag "superseded by newer guide" | Many guides are from the 1980s–90s |
| Know what the archive is called now | Repository successor/rename tracking | 2018–19 reforms split 档案局/档案馆; districts merged |
| Read surrounding local history | Wenshi ziliao articles for the same place shown alongside archive guides | Cross-linking the two collections is a major gain |
| Cite precisely | Stable record URLs, ARK, canvas deep link, "cite this" | |
| Work in English | Bilingual UI, pinyin/English search, translated fonds names (machine, flagged) | |

## 5. Gaps & revisions (status)

| # | Item | Status |
|---|---|---|
| 1 | "Full text" = TOCs + fonds lists, not whole handbooks; set expectations | Open: state it on the About page |
| 2 | Fonds as the primary search unit | Adopted (§3.3, §3.5) |
| 3 | Map as a derived view, not a source | **Resolved:** we maintain it and will retire it (§3.8) |
| 4 | Historical admin geography (code changes, CHGIS for pre-1949) | Adopted: `valid-from/to` on Place |
| 5 | Repositories as entities linked to Wikidata/authorities | Adopted |
| 6 | Rights | **Resolved:** derived metadata may be published (CC0). **Wenshi ziliao (2026-10-12):** publish the index (titles, authors, page numbers, series/issue) and transcribed index pages, as for the handbooks; no full texts. The site is for discovery and sends users to DPUL; link out to full-text platforms (中国文史资料集粹, CASS) where available. The CD-ROM index has no rights statement on the scanned pages or in the catalog record (DPUL: "Copyright Not Evaluated"); the physical disc/packaging was not checked. Boundaries: geoBoundaries is public domain |
| 7 | OCR quality plan (gold set, CER, QA, error reporting) | Adopted. Error reports flow into `corrections/` via GitHub issues or a simple form |
| 8 | Currency/access realities; moderated access notes; researcher safety | Open |
| 9 | Peer-library expansion method | §6 |
| 10 | Stakeholders/governance and the correction flow to DPUL | Partly addressed (§3.6); owners still to be named |
| 11 | Sustainability | Adopted: static site, Parquet canonical store, HF + DOI |
| 12 | Additional user groups | Adopted |
| 13 | Evaluation with real users | Adopted; the bake-off queries come from these sessions |
| 14 | Accessibility/i18n (WCAG 2.2 AA, list alternative to the map, `lang` tagging) | Adopted |
| 15 | **New:** access from mainland China. GitHub Pages, basemap CDNs, and font CDNs may be slow or blocked | Self-host fonts and JS. Test from China, and consider a mirror (e.g., a Chinese host or Princeton server) later |
| 16 | **New:** sensitive cartography (disputed borders, Taiwan) | **Decided (2026-10-12):** maps show only provinces that have guides ("no data, not on the map"), with no national outline. Drawing regions without data (e.g. Taiwan) would add a sovereignty statement and no information. Boundaries: geoBoundaries CHN ADM1 (public domain, via Wikimedia Commons), simplified, Albers projection. Caveat for the About page: geoBoundaries' Xinjiang includes Aksai Chin (de facto line; also claimed by India); Arunachal Pradesh is not included. The legacy ArcGIS map used the PRC official outline. Still open: the OpenFreeMap basemap draws its own country borders |
| 17 | **New:** cost of OCR at scale (~56k pages) | Engine chosen: DashScope. At ~4.2k tokens/page, ~56k pages ≈ 235M tokens. Price it from current DashScope rates; budget for re-runs when models or prompts change |

## 5a. Smoke-test findings (2026-10-11)

Sample: 8 handbooks + 2 wenshi ziliao items (2 locality volumes × 8 pages each), 220 scans, run end to end
(`make pipeline && make site`). Outputs: `data/export/report.md`, `data/export/ocr-compare.md`.

- **Incremental harvest works.** A re-run made conditional requests only (ETag → 304) and reported 10/10 unchanged. Images and OCR are fully cached.
- **The DashScope (`qwen3-vl-plus`) structured extraction is clearly better than rules.** It joins wrapped fonds names, handles count columns, and classifies page types. It yielded 1,914 fonds rows, 1,788 contents entries and 549 wenshi articles. Its transcriptions agree with RapidOCR at 0.91 mean character similarity. The main difference is that DashScope moves page numbers out of the text and into the structured fields, so prompt v2 should keep them in `lines`. It also returns dot leaders, which the export collapses.
- **Prompt v2 (HTML transcription):** tables keep their merged cells and columns; contents and wenshi pages render with leaders, authors and page numbers. It added a `holdings_table` page type (e.g. Jiang'an's holdings of reference materials 资料), so those tables no longer produce false contents entries. Out of 220 pages: 213 OK, 6 blocked by moderation, and 1 truncated at the 4,096-token output limit (→ RapidOCR). On 1 page the model left the HTML nearly empty and put everything in structured rows. A completeness check against RapidOCR caught it and rebuilt the HTML from the rows. Agreement with RapidOCR: 0.935 mean. ~4,760 tokens per page. Still to do: retry truncated pages with an HTML-only prompt (or split them); handle the thin empty header rows the model sometimes adds.
- **Cost:** ~4,350 tokens per page (~2.5k image tokens). The 220 scans took ~932k tokens, so ~56k scans ≈ 240M tokens.
- **⚠ Content moderation:** DashScope refused **6 of 220 pages (2.7%)** as "inappropriate content". One page lists fonds 建川238 "“八九”政治风波材料" (materials on 1989); others list Republican-era and Wang Jingwei regime (汪伪) courts, a list of schools, and a wenshi index. The pipeline records them as `blocked` and falls back to RapidOCR, so no text is lost and they're still searchable. However, **the pages most likely to be blocked are often the most valuable to researchers**. Plan: always keep a non-moderated engine; report blocked pages; evaluate open-weight Qwen VL run without moderation (e.g., on Princeton GPUs) for blocked pages or as the primary engine.
- **Pagefind:** CJK segmentation, simplified/traditional variants, pinyin/English titles, facets (Type, Province, Level, Published, Fonds cover) and per-scan sub-results all work. 10 guide pages produce a 6.1 MB site. Weak spot: queries are segmented into words that are matched independently (e.g. 四川省粮食 → 四川/省/粮食), so exact institution names don't always rank first. Add phrase-style queries to the bake-off.
- **Place matching:** 4 of 8 handbooks matched the legacy map by catalog ID, 2 by admin code, 2 only at province level (township/district not on the map). Guizhou wenshi didn't match at all, because the map's province layer has only the 18 provinces that have a provincial handbook. **A complete boundary dataset is needed before retiring the map.**
- **Catalog data:** wenshi ziliao dates carry MARC placeholders (`2010-1199`, `2011-1299`) as well as handbooks (`2004-699`). They are normalized and flagged.
- Not yet done: Parquet/HF upload, MARCXML harvest, JSON Schema check before the site build, CI for the pipeline, i18n.

## 6. Expansion beyond Princeton holdings

**Method**
1. Build the Princeton baseline: the set of `(repository code, guide title, edition, year)`.
2. Harvest candidate records from **WorldCat** (subject `Archives—China—{place}—Handbooks, manuals, etc.`, LC class **CD2051–2059**, and title keywords 档案馆指南 / 馆藏指南 / 全宗介绍 / 馆藏简介), plus **National Library of China** and **CALIS** catalogs.
3. Check targeted peers: Harvard-Yenching, Stanford (East Asia Library / Hoover), Berkeley C.V. Starr, Michigan, Chicago, Columbia, Library of Congress, **CUHK Universities Service Centre** (strong for wenshi ziliao), Leiden, SOAS, Tōyō Bunko.
4. Use reference compilations as checklists, e.g., national directories of archives and individual provincial archives' own published guides and websites.
5. Record a "not at Princeton" guide as a Guide with `held_by: [institutions]`, `source: worldcat|…`, `digitized: false`. Show it in search as **"known guide — not yet digitized here."**
6. For wenshi ziliao, link out to full-text platforms where available: 中国文史资料集粹 (licensed) and the CASS modern history platform (free, ~16,000 vols).
7. Peer harvests run in the same incremental pipeline (quarterly cadence).

**Output:** a gap report per province/level, which feeds acquisition and ILL/BorrowDirect requests.

## 7. Phasing

| Phase | Scope | Deliverable |
|---|---|---|
| 0 — Setup (2 wks) | Stakeholders; Figgy contact (crawl etiquette + ingest path for contributions); repo, CI, HF org | Agreed scope and owners |
| 1 — Harvest & metadata (3–4 wks) | Incremental harvester, joins on codes + MMS ID, date cleanup, ArcGIS reconciliation report | **v0.1 dataset on HF (metadata + geography)**; 11ty skeleton on GitHub Pages with Pagefind over metadata and a map view |
| 2 — OCR pilot (4–6 wks) | Gold set, model bake-off, run on handbooks (~10k pages) | Fonds/TOC text + structured fonds; CER report; **Pagefind bake-off decision** |
| 3 — Search MVP (4–6 wks) | Full-text + facets + map/draw search; user testing round 1; first DPUL contribution package | Public beta, **v0.2 dataset**; **ArcGIS map retired** once parity is reached |
| 4 — Wenshi ziliao at scale (6–8 wks) | ~45,800 pages → ~500k article entries | Cross-linked local history layer |
| 5 — Expansion | Peer-holdings harvest, gap report, Wikidata contributions | **v1.0 dataset** + DOI; desiderata list |
| Ongoing | Weekly harvest/diff, monthly releases, corrections review | Run reports, changelog |

## 8. Open questions
- What ingest path, if any, does Figgy/DPUL accept for OCR annotations and metadata enhancements? Who is the contact?
- What crawl rate and schedule is acceptable to Figgy/IIIF infrastructure?
- Which HF organization and dataset name should we use, and which DOI venue (Zenodo vs. DataSpace, or both)?
- ~~Where does OCR run?~~ **Resolved:** DashScope API (key in `.env`, never committed); RapidOCR locally as the baseline.
- Will the site get a custom domain (e.g., a princeton.edu subdomain) or stay on `*.github.io`?
- What license applies to the ArcGIS boundary polygons?

## 9. Immediate next steps
1. Scaffold the repo: `pipeline/` (uv project), `site/` (11ty), `corrections/`, and CI workflows (harvest-diff, build-deploy, release).
2. Write the incremental harvester (DPUL JSON + IIIF manifests + Figgy JSON-LD + MARCXML + ArcGIS GeoJSON) with snapshot/diff. Produce `guides.parquet` and a coverage-gap report.
3. Deploy a Pagefind + map prototype over metadata only to GitHub Pages. This gives an early check of Pagefind's handling of Chinese titles.
4. Select and hand-key the OCR gold set; start collecting real queries for the bake-off.
5. Contact the Figgy team about crawl etiquette and the contribution path.
