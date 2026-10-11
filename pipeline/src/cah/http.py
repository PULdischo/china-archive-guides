"""Polite HTTP client shared by all harvest steps."""

import time

import httpx

from .config import USER_AGENT

# Seconds between requests to Princeton hosts. Figgy offers no bulk export,
# so we harvest item by item and keep the load low.
DELAY = 0.5


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=60,
        follow_redirects=True,
        transport=httpx.HTTPTransport(retries=3),
    )


def get(c: httpx.Client, url: str, headers: dict | None = None, **params) -> httpx.Response:
    time.sleep(DELAY)
    r = c.get(url, headers=headers, params=params or None)
    if r.status_code != 304:
        r.raise_for_status()
    return r


def get_json(c: httpx.Client, url: str, **params):
    return get(c, url, **params).json()
