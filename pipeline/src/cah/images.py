"""Download page images from the IIIF Image API for OCR (local cache only)."""

from concurrent.futures import ThreadPoolExecutor

from . import http, records
from .config import IMAGES, OCR_IMAGE_WIDTH


def image_url(page: dict) -> str:
    width = min(OCR_IMAGE_WIDTH, page["width"] or OCR_IMAGE_WIDTH)
    return f"{page['image_service']}/full/{width},/0/default.jpg"


def run() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    todo = [p for _, _, p in records.all_pages(records.build())
            if not (IMAGES / f"{p['key']}.jpg").exists()]
    print(f"  {len(todo)} images to fetch")

    def fetch(page):
        r = http.get(c, image_url(page))
        tmp = IMAGES / f"{page['key']}.part"
        tmp.write_bytes(r.content)
        tmp.rename(IMAGES / f"{page['key']}.jpg")

    with http.client() as c, ThreadPoolExecutor(max_workers=3) as pool:
        for i, _ in enumerate(pool.map(fetch, todo), 1):
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}")
