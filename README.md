# China Archive & Local History Guides

Cross-search of Princeton's Chinese archival handbooks (档案馆指南) and wenshi ziliao (文史资料) indexes, with
full-text, metadata and spatial search, published as a static site and an open dataset. See [PLAN.md](PLAN.md).

**Status:** full handbook harvest in progress. Collections are defined in `pipeline/collections.toml`.

## Layout

```
pipeline/            Python (uv) — harvest, images, OCR, extraction, export
  collections.toml   which DPUL collections/items to process
  src/cah/           harvest.py  geo.py  records.py  images.py  ocr.py  extract.py  export.py
site/                11ty + MapLibre static site with a custom static search index
data/
  raw/ images/ ocr/ state/   local caches (git-ignored)
  export/dataset/    JSONL per entity (guides, pages, fonds, wenshi_articles, places)
  export/site/       site-ready guides.json + places.geojson
  export/report.md   coverage/quality report for the run
```

## Run

Requires `uv` and Node ≥ 18. Copy `.env.example` to `.env` and add a DashScope API key.

OCR engines (cached separately under `data/ocr/{engine}/`):
- `dashscope`: Qwen VL (`qwen3-vl-plus`) returns the transcription plus fonds/contents/article rows as JSON. Primary.
- `rapidocr`: local CPU OCR with rule-based extraction. Baseline.

`CAH_OCR_ENGINE` picks which output `export` uses; it falls back to RapidOCR for pages the preferred engine
lacks. `uv run cah compare` writes `data/export/ocr-compare.md` (engine agreement + token use).

```sh
make pipeline    # or: cd pipeline && uv run cah {harvest|geo|images|ocr|export|compare|all} [--engine dashscope|rapidocr]
make site        # 11ty build -> site/_site (search index from `cah csindex`)
make serve       # build and serve locally at http://localhost:8080
```

Re-running is incremental. Harvest uses conditional requests and content hashes. Images are fetched only when
missing. OCR is cached per canvas, keyed on the image hash and the engine version.

## Deploy

`.github/workflows/deploy-site.yml` builds the site from the committed `data/export/site/` and deploys it to
GitHub Pages. The pipeline itself is not yet in CI.
