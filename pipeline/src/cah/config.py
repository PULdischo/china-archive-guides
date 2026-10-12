"""Paths and sample configuration."""

import json
import os
import tomllib
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[2]
ROOT = PIPELINE_DIR.parent
DATA = ROOT / "data"
RAW = DATA / "raw"  # immutable source snapshots
STATE = DATA / "state"  # incremental-run bookkeeping
IMAGES = DATA / "images"  # OCR-resolution page images (never published)
OCR = DATA / "ocr"  # per-canvas OCR cache
EXPORT = DATA / "export"  # dataset + site-ready outputs

COLLECTIONS_FILE = PIPELINE_DIR / "collections.toml"

USER_AGENT = "china-archive-guides/0.1 (Princeton University Library research project)"

# Width (px) requested from the IIIF Image API for OCR.
OCR_IMAGE_WIDTH = 1500


def load_env(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from .env without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env()


def load_collections() -> dict:
    with COLLECTIONS_FILE.open("rb") as f:
        return tomllib.load(f)


def collection_ids(kind: str, cfg: dict) -> list[str]:
    """Item IDs for a collection: the explicit list, or the last harvested browse listing."""
    if "browse" in cfg:
        return read_json(RAW / "listing" / f"{kind}.json", {"ids": []})["ids"]
    return cfg["ids"]


def processing_config(cfg: dict, staged: bool = False) -> tuple[list[str] | None, dict]:
    """IDs and limits for steps after harvest. A collection at stage "harvest" only passes its
    sample on, unless `staged` asks for everything (e.g. for prompt tests)."""
    if cfg.get("stage") == "harvest" and not staged:
        return cfg["sample"]["ids"], cfg["sample"]
    return None, cfg


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
