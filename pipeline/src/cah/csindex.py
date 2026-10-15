"""Static search index for the site (replaced Pagefind, 2026-10-15).

Pagefind scored and built a result for every matching record (~0.3 ms each), so very common terms
(学校: 6,152 pages) took seconds. Our search only needs set intersection, per-unit counts and exact
verification of the displayed pages, so this index stores just that (学校: ~0.3 s cold in a browser):

  csi/units.json        one entry per unit (handbook or wenshi volume): title, href, year, filters
  csi/docs.bin          per document: unit index (uint16) + page number (uint16), little-endian
  csi/p/{shard}.bin     postings, sharded by FNV-1a(token) % SHARDS:
                        u32 count, then per token: u8 len, token (ASCII), varint n, n varint gaps
  csi/d/{doc}.json      {"t": page text, "v": DPUL viewer URL} for verification and excerpts

Documents are the search records (records.jsonl from search.py): scanned pages plus one title
record per unit. Tokens are the encoded unigrams/bigrams/words already in each record's content.
"""

import json
import shutil
import struct
from collections import defaultdict

from .config import EXPORT

OUT = EXPORT / "csi"
SHARDS = 4096


def fnv1a(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("ascii"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def run() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "p").mkdir(parents=True)
    (OUT / "d").mkdir()

    units, unit_ix = [], {}
    docs = bytearray()
    postings = defaultdict(list)
    n_docs = 0
    with (EXPORT / "search" / "records.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            m = r["meta"]
            uid = m["guide"]
            if uid not in unit_ix:
                unit_ix[uid] = len(units)
                units.append({"id": uid, "href": m["href"], "title": m["title"], "year": m.get("year") or "",
                              "dpul": m.get("dpul"),
                              "filters": {k: v for k, v in r["filters"].items() if k not in ("guide", "record")}})
            page_n = int(r["url"].rsplit("#p", 1)[1]) if "#p" in r["url"] else 0
            docs += struct.pack("<HH", unit_ix[uid], page_n)
            for tok in set(r["content"].split()):
                postings[tok].append(n_docs)
            (OUT / "d" / f"{n_docs}.json").write_text(
                json.dumps({"t": m.get("text") or "", "v": m.get("viewer"), "p": m.get("page")},
                           ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            n_docs += 1

    shards = defaultdict(list)
    for tok, ids in postings.items():
        shards[fnv1a(tok) % SHARDS].append(tok)
    total = 0
    for s in range(SHARDS):
        buf = bytearray(struct.pack("<I", len(shards[s])))
        for tok in sorted(shards[s]):
            ids = postings[tok]  # already ascending (documents were appended in order)
            t = tok.encode("ascii")
            buf += bytes([len(t)]) + t + varint(len(ids))
            prev = -1
            for d in ids:
                buf += varint(d - prev - 1)
                prev = d
        (OUT / "p" / f"{s}.bin").write_bytes(buf)
        total += len(buf)

    (OUT / "docs.bin").write_bytes(docs)
    (OUT / "units.json").write_text(json.dumps({"shards": SHARDS, "docs": n_docs, "units": units},
                                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copy(EXPORT / "search" / "t2s.json", OUT / "t2s.json")
    print(f"  {n_docs:,} documents, {len(units):,} units, {len(postings):,} tokens, "
          f"postings {total / 1e6:.1f} MB in {SHARDS} shards")
