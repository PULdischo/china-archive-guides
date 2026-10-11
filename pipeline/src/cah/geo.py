"""Import the legacy ArcGIS map layers (we maintain them; to be retired).

The layers are seed/validation data: each place feature lists up to four
handbooks as wide Title_n / Year_n / URL_n columns, where URL_n is a Princeton
catalog record. We melt those into a catalog-ID -> place lookup.
"""

from . import http
from .config import RAW, read_json, write_json

SERVICES = "https://services.arcgis.com/4Ko8f1mCWFLyY4NV/arcgis/rest/services"
LAYERS = {
    "national": "China National Archives January 2024",
    "province": "China Provinces Archvies January 2024",
    "prefecture": "China Prefectural Archives January 2024",
    "county": "China County Archives January 2024",
}
CODE_FIELD = {"province": "Provinc_Co", "prefecture": "Prefect_Co", "county": "County_Cod"}


def run() -> None:
    with http.client() as c:
        for level, name in LAYERS.items():
            gj = http.get_json(
                c,
                f"{SERVICES}/{name}/FeatureServer/0/query",
                where="1=1",
                outFields="*",
                outSR="4326",
                maxAllowableOffset="0.005",  # degrees; simplify for the web map
                geometryPrecision="4",
                f="geojson",
            )
            write_json(RAW / "arcgis" / f"{level}.geojson", gj)
            print(f"  {level:10} {len(gj['features'])} features")


def places() -> list[dict]:
    """All map places as flat records with their linked catalog IDs."""
    out = []
    for level in LAYERS:
        gj = read_json(RAW / "arcgis" / f"{level}.geojson", {"features": []})
        for f in gj["features"]:
            a = f["properties"]
            code = a.get(CODE_FIELD.get(level, ""), "") or ("0" if level == "national" else "")
            links = [
                {"title": a[f"Title_{n}"], "year": a.get(f"Year_{n}"), "url": a[f"URL_{n}"],
                 "mms_id": a[f"URL_{n}"].rstrip("/").rsplit("/", 1)[-1]}
                for n in range(1, 5)
                if a.get(f"Title_{n}") and a.get(f"URL_{n}")
            ]
            out.append({
                "id": f"{level}:{code}",
                "level": level,
                "code": str(code),
                "name_zh": a.get("Chinese_Na") or "国家档案馆",
                "name_pinyin": a.get("Pinyin_Nam") or a.get("English_Na"),
                "name_en": a.get("English_Na"),
                "province_zh": a.get("Provinc_CH") or (a.get("Chinese_Na") if level == "province" else None),
                "province_en": a.get("Provinc_EN") or a.get("English_Na"),
                "links": links,
                "geometry": f.get("geometry"),
            })
    return out
