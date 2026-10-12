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
    fetch_boundaries()


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


# --- Provinces ---------------------------------------------------------------
# GB/T 2260 provincial-level codes. Boundaries: geoBoundaries CHN ADM1 (public domain,
# via Wikimedia Commons). Only provinces that have guides are drawn, with no national
# outline: no data -> not on the map (a deliberate choice; see the map captions / About page).
PROVINCES = {
    "11": ("北京", "Beijing"), "12": ("天津", "Tianjin"), "13": ("河北", "Hebei"), "14": ("山西", "Shanxi"),
    "15": ("内蒙古", "Inner Mongolia"), "21": ("辽宁", "Liaoning"), "22": ("吉林", "Jilin"),
    "23": ("黑龙江", "Heilongjiang"), "31": ("上海", "Shanghai"), "32": ("江苏", "Jiangsu"),
    "33": ("浙江", "Zhejiang"), "34": ("安徽", "Anhui"), "35": ("福建", "Fujian"), "36": ("江西", "Jiangxi"),
    "37": ("山东", "Shandong"), "41": ("河南", "Henan"), "42": ("湖北", "Hubei"), "43": ("湖南", "Hunan"),
    "44": ("广东", "Guangdong"), "45": ("广西", "Guangxi"), "46": ("海南", "Hainan"), "50": ("重庆", "Chongqing"),
    "51": ("四川", "Sichuan"), "52": ("贵州", "Guizhou"), "53": ("云南", "Yunnan"), "54": ("西藏", "Tibet"),
    "61": ("陕西", "Shaanxi"), "62": ("甘肃", "Gansu"), "63": ("青海", "Qinghai"), "64": ("宁夏", "Ningxia"),
    "65": ("新疆", "Xinjiang"), "71": ("台湾", "Taiwan"), "81": ("香港", "Hong Kong"), "82": ("澳门", "Macau"),
}
NATIONAL = {"code": "00", "zh": "中央/全国", "en": "National archives"}
# geoBoundaries shapeName -> code (its Guangdong is labelled "Guangzhou Province").
SHAPE_NAMES = {"Guangzhou Province": "44", "Inner Mongolia Autonomous Region": "15", "Tibet Autonomous Region": "54",
               "Ningxia Ningxia Hui Autonomous Region": "64", "Hong Kong Special Administrative Region": "81",
               "Macau Special Administrative Region": "82"}
BOUNDARIES_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/CHN/ADM1/"
                  "geoBoundaries-CHN-ADM1_simplified.geojson")
BOUNDARIES = RAW / "boundaries" / "CHN-ADM1_simplified.geojson"


def fetch_boundaries() -> None:
    if not BOUNDARIES.exists():
        with http.client() as c:
            write_json(BOUNDARIES, http.get_json(c, BOUNDARIES_URL))


def province_of(guide: dict) -> dict | None:
    """Province from the handbook's admin code, else its matched places, else its place name."""
    code = (guide.get("admin_code") or "")[:2]
    if code == "1" or (guide.get("admin_code") or "").startswith("10"):
        return NATIONAL
    if code not in PROVINCES:
        codes = [p["code"][:2] for p in guide.get("places", []) if p["code"][:2] in PROVINCES]
        code = codes[0] if codes else ""
    if code not in PROVINCES:
        for spatial in guide.get("spatial", []):
            name = spatial.split(" (")[0].split(" ")[0]
            code = next((c for c, (_, en) in PROVINCES.items() if en == name), "")
            if code:
                break
    if code not in PROVINCES:
        return None
    zh, en = PROVINCES[code]
    return {"code": code, "zh": zh, "en": en}


def _albers(lon: float, lat: float) -> tuple[float, float]:
    """Albers equal-area conic for China (standard parallels 25°N / 47°N, central meridian 105°E)."""
    import math
    p1, p2, lon0 = math.radians(25), math.radians(47), math.radians(105)
    n = (math.sin(p1) + math.sin(p2)) / 2
    c = math.cos(p1) ** 2 + 2 * n * math.sin(p1)
    rho0 = math.sqrt(c) / n
    rho = math.sqrt(c - 2 * n * math.sin(math.radians(lat))) / n
    theta = n * (math.radians(lon) - lon0)
    return rho * math.sin(theta), -(rho0 - rho * math.cos(theta))


def province_shapes(codes: set[str], width: int = 1000) -> dict:
    """SVG path data for the given provinces, projected and simplified, fitted to `width`."""
    from shapely.geometry import shape
    from shapely.ops import transform

    gj = read_json(BOUNDARIES)
    geoms = {}
    for f in gj["features"]:
        name = f["properties"]["shapeName"]
        code = SHAPE_NAMES.get(name) or next(
            (c for c, (_, en) in PROVINCES.items() if name.split(" ")[0] == en.split(" ")[0]), None)
        if code in codes:
            geoms[code] = transform(lambda x, y, z=None: _albers(x, y), shape(f["geometry"]))
    if not geoms:
        return {"viewBox": "0 0 1 1", "paths": {}, "labels": {}}
    minx = min(g.bounds[0] for g in geoms.values()); miny = min(g.bounds[1] for g in geoms.values())
    maxx = max(g.bounds[2] for g in geoms.values()); maxy = max(g.bounds[3] for g in geoms.values())
    scale = width / (maxx - minx)
    height = round((maxy - miny) * scale)

    def path(geom) -> str:
        geom = geom.simplify(0.6 / scale, preserve_topology=True)
        polys = getattr(geom, "geoms", [geom])
        parts = []
        for poly in polys:
            for ring in [poly.exterior, *poly.interiors]:
                pts = [((x - minx) * scale, (y - miny) * scale) for x, y in ring.coords]
                if len(pts) >= 4:
                    parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts[:-1]) + "Z")
        return "".join(parts)

    labels = {}
    for code, g in geoms.items():
        pt = g.representative_point()
        labels[code] = [round((pt.x - minx) * scale, 1), round((pt.y - miny) * scale, 1)]
    return {"viewBox": f"0 0 {width} {height}", "paths": {c: path(g) for c, g in geoms.items()}, "labels": labels}


def province_features(codes: set[str]) -> list[dict]:
    """GeoJSON features (lon/lat, simplified) for the given provinces, for the web map."""
    from shapely.geometry import mapping, shape

    out = []
    for f in read_json(BOUNDARIES)["features"]:
        name = f["properties"]["shapeName"]
        code = SHAPE_NAMES.get(name) or next(
            (c for c, (_, en) in PROVINCES.items() if name.split(" ")[0] == en.split(" ")[0]), None)
        if code in codes:
            geom = shape(f["geometry"]).simplify(0.01, preserve_topology=True)
            out.append({"code": code, "geometry": mapping(geom)})
    return out
