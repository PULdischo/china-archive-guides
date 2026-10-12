"""OCR engines with per-engine caches: data/ocr/{engine}/{canvas}.json.

- rapidocr: PaddleOCR models on ONNX, local CPU. Returns line boxes; structure
  comes from the rule-based extractors (extract.py). Baseline for the bake-off.
- dashscope: Qwen VL via DashScope's OpenAI-compatible API. One call per page
  returns an HTML transcription (tables keep their merged cells) plus
  fonds/contents/article rows as JSON.

Cache entries are keyed on (image hash, engine version), where the version
includes the model and prompt version, so re-runs only touch new images or a
changed engine.
"""

import base64
import hashlib
import importlib.metadata
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import records
from .config import IMAGES, OCR, read_json, write_json

KIND_DESC = {
    "handbook": "a Chinese archival handbook (档案馆指南): table of contents and/or fonds list (全宗介绍/全宗名册)",
    "wenshi": "a wenshi ziliao (文史资料) general index: series titles, issue (辑) headings, and article entries",
}


def cache_path(engine: str, key: str):
    return OCR / engine / f"{key}.json"


def load(engine: str, key: str) -> dict | None:
    return read_json(cache_path(engine, key))


class RapidOCREngine:
    name = "rapidocr"
    workers = 1  # onnxruntime already uses all cores

    def __init__(self):
        self.version = f"rapidocr-onnxruntime {importlib.metadata.version('rapidocr-onnxruntime')}"
        self._ocr = None

    def version_for(self, guide: dict) -> str:
        return self.version

    def __call__(self, img_path, guide: dict) -> dict:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = self._ocr or RapidOCR()
        result, _ = self._ocr(str(img_path))
        return {"lines": [
            {"text": text, "score": round(float(score), 4),
             "box": [[round(float(x)), round(float(y))] for x, y in box]}
            for box, text, score in (result or [])
        ]}


class DashScopeEngine:
    name = "dashscope"
    workers = int(os.environ.get("DASHSCOPE_WORKERS", "8"))
    PROMPT_VERSION = 2
    SYSTEM = ("You transcribe and structure scanned pages of Chinese archival handbooks (档案馆指南) "
              "and wenshi ziliao (文史资料) indexes. Reply with a single JSON object only.")
    HTML_RULES = """HTML rules:
- Allowed tags only: h2 h3 h4 p br table caption thead tbody tr th td ul ol li span. No styles or ids.
  Allowed attributes: rowspan, colspan, class.
- Tables: reproduce every column, header row and merged cell (rowspan/colspan) as printed. Empty cells stay empty.
- Contents lists: section headings as h3/h4; entries as <ul class="toc"><li>heading <span class="pg">page</span></li></ul>.
  Omit dot leaders.
- Wenshi ziliao index: series titles as h3, issue headings (第N辑) as h4, entries as
  <ul class="toc"><li>title <span class="au">author</span> <span class="pg">page</span></li></ul>; other lines
  (出版, 印制时间) as <p>.
- Running headers/footers (book title, printed page number) as <p class="running">.
- Copy characters exactly as printed (simplified or traditional). Do not translate, normalize or add anything."""
    PROMPT = """This page is from {kind}.
Return one JSON object:
{{
  "page_type": "toc" | "fonds_table" | "holdings_table" | "wenshi_index" | "prose" | "front_matter" | "blank" | "other",
  "html": "<the whole page transcribed as simple HTML>",
  "toc": [{{"heading": str, "section": str|null, "page": int|null}}],
  "fonds": [{{"fonds_no": str, "name": str, "counts": [int], "date_start": int|null, "date_end": int|null}}],
  "articles": [{{"series": str|null, "issue": int|null, "title": str, "author": str|null, "page": int|null}}]
}}
{rules}
Structured lists (use [] when they do not apply; the HTML already carries everything else):
- "toc": only for a table of contents (目录): entries that point to a page of the book.
- "fonds": only rows of a fonds register (全宗号 / 全宗名称 / counts such as 目录数, 案卷数 / 起止年代).
  Not for holdings of reference materials (资料) or other tables.
- "articles": only wenshi ziliao index entries. "series"/"issue": the series title and 第N辑 number in effect,
  when shown on this page; otherwise null."""
    # Retry when the full answer is unusable (over the output limit, or the model looped on
    # empty table cells): HTML only, with a guard against repetition.
    PROMPT_HTML_ONLY = """This page is from {kind}.
Return one JSON object: {{"page_type": str, "html": "<the whole page transcribed as simple HTML>"}}
{rules}
- Never repeat empty rows or cells: if a table has blank rows, omit them. Do not use &nbsp;."""

    def __init__(self):
        self.key = os.environ.get("DASHSCOPE_API_KEY")
        if not self.key:
            raise SystemExit("DASHSCOPE_API_KEY is not set (add it to .env; see .env.example)")
        self.base = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        self.model = os.environ.get("DASHSCOPE_MODEL", "qwen3-vl-plus")
        self.version = f"dashscope {self.model} prompt-v{self.PROMPT_VERSION}"
        self.http = httpx.Client(timeout=180, transport=httpx.HTTPTransport(retries=3),
                                 headers={"Authorization": f"Bearer {self.key}"})

    def _post(self, b64: str, prompt: str, **params) -> httpx.Response:
        body = {
            "model": self.model,
            "temperature": 0,
            **params,
            "max_tokens": 4096,  # DashScope's default ceiling for this model
            "messages": [
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ]},
            ],
        }
        for attempt in range(6):
            r = self.http.post(f"{self.base}/chat/completions", json=body)
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
            time.sleep(min(60, 2 ** attempt * 3))  # rate limit / transient server error
        return r

    def _parse(self, r: httpx.Response) -> tuple[dict, dict | None]:
        data = r.json()
        choice = data["choices"][0]
        meta = {"model": data.get("model", self.model), "usage": data.get("usage"),
                "finish_reason": choice.get("finish_reason")}
        raw = choice["message"]["content"]
        try:
            return meta, json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        except json.JSONDecodeError as e:
            return {**meta, "error": f"unparseable JSON: {e}", "raw": raw}, None

    def version_for(self, guide: dict) -> str:
        """Cache version: wenshi pages use their own HTML-only prompt (cah.wenshi)."""
        if guide["kind"] == "wenshi":
            from .wenshi import PROMPT_V3_VERSION
            return f"dashscope {self.model} {PROMPT_V3_VERSION}"
        return self.version

    def __call__(self, img_path, guide: dict) -> dict:
        b64 = base64.b64encode(img_path.read_bytes()).decode()
        kind = KIND_DESC[guide["kind"]]
        if guide["kind"] == "wenshi":
            from .wenshi import PROMPT_V3
            prompt, retry = PROMPT_V3, PROMPT_V3  # HTML only; articles are parsed at export
        else:
            prompt = self.PROMPT.format(kind=kind, rules=self.HTML_RULES)
            retry = self.PROMPT_HTML_ONLY.format(kind=kind, rules=self.HTML_RULES)
        r = self._post(b64, prompt)
        if r.status_code == 400 and "inappropriate content" in r.text:
            # DashScope content moderation. Unpredictable on archival material (e.g. fonds on 1989);
            # record it so the page isn't retried every run and export falls back to RapidOCR.
            return {"blocked": "moderation", "error": r.text[:300]}
        if r.status_code >= 400:
            raise RuntimeError(f"DashScope {r.status_code}: {r.text[:300]}")
        meta, parsed = self._parse(r)
        if parsed is None:
            r2 = self._post(b64, retry, presence_penalty=0.5)
            if r2.status_code < 400:
                meta2, parsed = self._parse(r2)
                usage = [u for u in (meta.get("usage"), meta2.get("usage")) if u]
                meta = {**meta2, "retry": "html_only",
                        "usage": {"total_tokens": sum(u.get("total_tokens", 0) for u in usage)}}
        if parsed is None:
            return meta
        return {**meta, **{k: parsed.get(k) or [] for k in ("toc", "fonds", "articles")},
                "html": parsed.get("html") or "", "page_type": parsed.get("page_type")}


ENGINES = {"rapidocr": RapidOCREngine, "dashscope": DashScopeEngine}


def needs_fallback(key: str) -> bool:
    """A page the primary engine (DashScope) has no usable result for."""
    o = load("dashscope", key)
    return o is None or "error" in o


def run(engine_name: str | None = None, fallback_only: bool = False) -> None:
    engine = ENGINES[engine_name or os.environ.get("CAH_OCR_ENGINE", "rapidocr")]()
    todo = []
    for g, _, p in records.all_pages(records.build()):
        img = IMAGES / f"{p['key']}.jpg"
        if not img.exists() or (fallback_only and not needs_fallback(p["key"])):
            continue
        img_hash = hashlib.sha256(img.read_bytes()).hexdigest()
        cached = load(engine.name, p["key"])
        if cached and cached["image_sha256"] == img_hash and cached["engine"] == engine.version_for(g) \
                and ("error" not in cached or cached.get("blocked")):
            continue
        todo.append((g, p, img, img_hash))
    print(f"  {engine.name}: {len(todo)} pages to OCR")

    def work(item):
        g, p, img, img_hash = item
        try:
            result = engine(img, g)
        except Exception as e:  # keep going; failed pages are retried next run
            result = {"error": str(e)}
        write_json(cache_path(engine.name, p["key"]),
                   {"engine": engine.version_for(g), "image_sha256": img_hash, "canvas_id": p["canvas_id"], **result})
        return result

    errors, blocked, tokens = 0, 0, 0
    with ThreadPoolExecutor(max_workers=engine.workers) as pool:
        for i, result in enumerate(pool.map(work, todo), 1):
            blocked += bool(result.get("blocked"))
            errors += "error" in result and not result.get("blocked")
            tokens += (result.get("usage") or {}).get("total_tokens", 0)
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}")
    print(f"  done: {len(todo) - errors - blocked} ok, {blocked} blocked by moderation, {errors} errors"
          + (f", {tokens:,} tokens" if tokens else ""))
