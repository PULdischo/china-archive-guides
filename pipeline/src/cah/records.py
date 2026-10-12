"""Normalize raw DPUL records + IIIF manifests into Guide/Page records."""

import re
from urllib.parse import quote

from . import geo
from .config import RAW, collection_ids, load_collections, processing_config, read_json

ARK_RESOLVER = "http://arks.princeton.edu/"
FIGGY_VIEWER = "https://figgy.princeton.edu/viewer#?manifest={manifest}&cv={index}"


def first(doc: dict, field: str):
    v = doc.get(f"readonly_{field}_ssim")
    return v[0] if v else None


def all_(doc: dict, field: str) -> list:
    return doc.get(f"readonly_{field}_ssim") or []


def parse_year(raw: str | None) -> tuple[int | None, bool]:
    """Return (year, malformed). MARC 'u' placeholders surface as e.g. '2004-699'."""
    if not raw:
        return None, True
    m = re.match(r"^(\d{4})", raw)
    return (int(m.group(1)) if m else None), not re.fullmatch(r"\d{4}", raw)


def parse_codes(doc: dict) -> dict:
    """Handbooks carry '43 (行政区划代码).' and '443001 (档案馆代码).' in description."""
    codes = {}
    for d in all_(doc, "description"):
        if m := re.match(r"^\s*(\d+)\s*\(行政区划代码\)", d):
            codes["admin_code"] = m.group(1)
        if m := re.match(r"^\s*(\d+)\s*\(档案馆代码\)", d):
            codes["archive_code"] = m.group(1)
    return codes


def split_title(t: str | None) -> str | None:
    """Drop the statement of responsibility (' / editor')."""
    return t.split(" / ")[0].rstrip(". ") if t else None


def pages_of(manifest: dict, max_pages: int, start_index: int, volume: int) -> list[dict]:
    canvases = manifest["sequences"][0]["canvases"]
    if max_pages:
        canvases = canvases[:max_pages]
    pages = []
    for i, cv in enumerate(canvases):
        res = cv["images"][0]["resource"]
        pages.append({
            "n": start_index + i + 1,  # running page number within the guide (anchor #p{n})
            "volume": volume,
            "canvas_index": i,  # index within its own manifest (viewer deep link)
            "canvas_id": cv["@id"],
            "key": cv["@id"].rstrip("/").rsplit("/", 1)[-1],  # Figgy canvas UUID
            "label": str(cv.get("label", i + 1)),
            "image_service": res["service"]["@id"],
            "width": cv.get("width"),
            "height": cv.get("height"),
            "viewer_url": FIGGY_VIEWER.format(manifest=quote(manifest["@id"], safe=""), index=i),
        })
    return pages


def match_places(guide: dict, places: list[dict]) -> tuple[list[dict], str | None]:
    """Link a guide to map places: catalog ID first, then admin code, then place name."""
    by_mms = [p for p in places if any(l["mms_id"] == guide["mms_id"] for l in p["links"])]
    if by_mms:
        return by_mms, "catalog_id"
    if code := guide.get("admin_code"):
        by_code = [p for p in places if p["code"] == code]
        if by_code:
            return by_code, "admin_code"
    for spatial in guide["spatial"]:
        name = spatial.split(" (")[0].split(" ")[0]  # 'Guizhou Sheng (China)' -> 'Guizhou'
        by_name = [p for p in places if p["level"] == "province" and (p["name_en"] or "").startswith(name)]
        if by_name:
            return by_name, "place_name"
    return [], None


def build(staged: bool = False) -> list[dict]:
    collections = load_collections()
    places = geo.places()
    guides = []
    for kind, cfg in collections.items():
        only, cfg = processing_config(cfg, staged)
        for item_id in only or collection_ids(kind, cfg):
            doc = read_json(RAW / "dpul" / f"{item_id}.json")
            mf = read_json(RAW / "manifests" / f"{item_id}.json")
            if doc is None or mf is None:  # not harvested (yet)
                continue
            titles = all_(doc, "title")
            catalog = first(doc, "view-in-catalog") or ""
            mms = re.search(r"catalog/(\d+)", catalog)
            date_raw = first(doc, "date")
            year, malformed = parse_year(date_raw)
            manifest_url = doc["content_metadata_iiif_manifest_field_ssi"]
            ark = first(doc, "identifier")

            g = {
                "id": item_id,
                "kind": kind,
                "title_zh": split_title(titles[0] if titles else None),
                "title_romanized": split_title(titles[1] if len(titles) > 1 else None),
                "title_full": titles,
                "editor": all_(doc, "editor") or all_(doc, "contributor"),
                "publisher": all_(doc, "publisher"),
                "date_raw": date_raw,
                "year": year,
                "date_malformed": malformed,
                "call_number": first(doc, "call-number"),
                "extent": first(doc, "extent"),
                "spatial": all_(doc, "spatial"),
                "subjects": all_(doc, "subject"),
                "rights": first(doc, "edm-rights"),
                **parse_codes(doc),
                "ark": ark,
                "mms_id": mms.group(1) if mms else None,
                "figgy_id": manifest_url.rstrip("/").split("/")[-2],
                "urls": {
                    "dpul": f"https://dpul.princeton.edu/ealtoc/catalog/{item_id}",
                    "catalog": f"https://catalog.princeton.edu/catalog/{mms.group(1)}" if mms else None,
                    "ark": ARK_RESOLVER + ark if ark else None,
                    "manifest": manifest_url,
                },
                "dpul_updated_at": doc.get("system_updated_at_dtsi"),
            }

            volumes, n = [], 0
            manifests = (mf["volumes"] or [mf["top"]])[: cfg.get("max_volumes") or None]
            total = len(mf["top"].get("manifests", [])) or 1
            for vi, m in enumerate(manifests):
                pages = pages_of(m, cfg.get("max_pages_per_volume", 0), n, vi)
                n += len(pages)
                label = m.get("label")
                volumes.append({
                    "index": vi,
                    "id": m["@id"].rstrip("/").split("/")[-2],  # Figgy resource UUID (stable)
                    "label": label[0] if isinstance(label, list) else label,
                    "manifest": m["@id"],
                    "canvas_count": len(m["sequences"][0]["canvases"]),
                    "pages": pages,
                })
            g["volumes"] = volumes
            g["volumes_total"] = total
            g["volumes_restricted"] = mf.get("restricted", [])  # e.g. 403 in Figgy
            g["volumes_sampled"] = len(volumes) < total or any(
                len(v["pages"]) < v["canvas_count"] for v in volumes
            )

            matched, how = match_places(g, places)
            g["places"] = [{k: p[k] for k in ("id", "level", "code", "name_zh", "name_pinyin", "province_zh")}
                           for p in matched]
            g["place_match"] = how
            guides.append(g)
    return guides


def all_pages(guides: list[dict]):
    for g in guides:
        for v in g["volumes"]:
            for p in v["pages"]:
                yield g, v, p
