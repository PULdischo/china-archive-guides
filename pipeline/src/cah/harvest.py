"""Harvest DPUL records and IIIF manifests into raw snapshots.

Incremental: manifests are fetched with If-None-Match (Figgy returns ETags), and
each item's content hash is compared with the previous run so later steps can
reprocess only what changed. DPUL's own timestamps move on reindexing, so the
hash, not the timestamp, decides.
"""

import hashlib
import json

from . import http
from .config import RAW, STATE, collection_ids, load_collections, read_json, write_json

DPUL_ITEM = "https://dpul.princeton.edu/ealtoc/catalog/{id}.json"
DPUL_BROWSE = "https://dpul.princeton.edu/ealtoc/browse/{slug}.json"

# Fields that change on every reindex without any content change.
VOLATILE = {"timestamp", "_version_", "system_updated_at_dtsi"}


def content_hash(doc: dict, manifests: dict) -> str:
    stable = {k: v for k, v in doc.items() if k not in VOLATILE}
    blob = json.dumps([stable, manifests], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def fetch_manifest(c, url: str, etags: dict, cache: dict) -> dict:
    """GET a manifest, reusing the cached copy when the server answers 304."""
    headers = {"If-None-Match": etags[url]} if url in etags and url in cache else None
    r = http.get(c, url, headers=headers)
    if r.status_code == 304:
        return cache[url]
    if etag := r.headers.get("etag"):
        etags[url] = etag
    return r.json()


def harvest_item(c, item_id: str, cfg: dict, etags: dict) -> tuple[dict, dict]:
    doc = http.get_json(c, DPUL_ITEM.format(id=item_id))["response"]["document"]
    prev = read_json(RAW / "manifests" / f"{item_id}.json", {})
    cache = {m["@id"]: m for m in [prev.get("top"), *prev.get("volumes", [])] if m}

    top = fetch_manifest(c, doc["content_metadata_iiif_manifest_field_ssi"], etags, cache)
    volumes = []
    # Wenshi ziliao items are IIIF collections with one manifest per locality.
    for ref in top.get("manifests", [])[: cfg.get("max_volumes") or None]:
        volumes.append(fetch_manifest(c, ref["@id"], etags, cache))
    return doc, {"top": top, "volumes": volumes}


def list_browse(c, kind: str, slug: str) -> list[str]:
    """Enumerate a DPUL browse category (JSON:API pages of 100) and save the listing."""
    ids, page = [], 1
    while page:
        d = http.get_json(c, DPUL_BROWSE.format(slug=slug), per_page=100, page=page)
        ids += [x["links"]["self"].rstrip("/").rsplit("/", 1)[-1] for x in d["data"]]
        page = d["meta"]["pages"].get("next_page")
    write_json(RAW / "listing" / f"{kind}.json", {"browse": slug, "ids": ids})
    print(f"  {kind}: {len(ids)} items in browse/{slug}")
    return ids


def run() -> dict:
    collections = load_collections()
    state = read_json(STATE / "harvest.json", {"items": {}, "etags": {}})
    report = {"new": [], "changed": [], "unchanged": [], "failed": [], "removed": []}

    with http.client() as c:
        for kind, cfg in collections.items():
            ids = list_browse(c, kind, cfg["browse"]) if "browse" in cfg else collection_ids(kind, cfg)
            for n, item_id in enumerate(ids, 1):
                try:
                    doc, manifests = harvest_item(c, item_id, cfg, state["etags"])
                except Exception as e:  # keep going; retried next run
                    report["failed"].append(item_id)
                    print(f"  failed    {kind:8} {item_id}: {e}")
                    continue
                h = content_hash(doc, manifests)
                prev = state["items"].get(item_id)
                status = "new" if prev is None else "unchanged" if prev["hash"] == h else "changed"
                report[status].append(item_id)

                write_json(RAW / "dpul" / f"{item_id}.json", doc)
                write_json(RAW / "manifests" / f"{item_id}.json", manifests)
                state["items"][item_id] = {
                    "kind": kind,
                    "hash": h,
                    "dpul_updated_at": doc.get("system_updated_at_dtsi"),
                }
                if status != "unchanged" or n % 50 == 0:
                    print(f"  {status:9} {kind:8} {item_id}  ({n}/{len(ids)})")
            # Items that left a browse listing are reported, not deleted (tombstones come with releases).
            if "browse" in cfg:
                gone = [i for i, v in state["items"].items() if v["kind"] == kind and i not in set(ids)]
                report["removed"] += gone

    write_json(STATE / "harvest.json", state)
    print({k: len(v) for k, v in report.items()})
    return report
