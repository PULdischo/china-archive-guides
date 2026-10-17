"""Provisional structured extraction from OCR lines.

Rule-based placeholders for the smoke test: group OCR boxes into visual rows,
then match fonds-table rows (handbooks) and "title……author(page)" index rows
(wenshi ziliao). Every extracted record points back to its canvas.
"""

import re
from statistics import median

YEAR = r"(?:1[6-9]\d\d|20\d\d)"
# Fonds register row: number (may carry a prefix like 清1), name, optional count
# columns (目录数, 案卷数), then a date range (separator sometimes lost in OCR).
FONDS_ROW = re.compile(
    rf"^[.\s]*(?P<no>[\u4e00-\u9fff]?\d{{1,4}})\s+(?P<name>\S.*?)(?P<counts>(?:\s+\d{{1,6}}){{0,3}})"
    rf"\s+(?P<start>{YEAR})\s*[~～—\-–至]*\s*(?P<end>{YEAR})$"
)
LEADER = r"[…⋯‥·.。]"
# Contents row: heading, dot leaders, page number in parentheses.
TOC_ROW = re.compile(rf"^(?P<heading>\S.*?)\s*{LEADER}*\s*[（(]\s*(?P<page>\d{{1,4}})\s*[)）]$")
ARTICLE_ROW = re.compile(rf"^(?P<title>.+?)\s*{LEADER}{{3,}}\s*(?P<author>[^…⋯·.]*?)\s*[（(]\s*(?P<page>\d+)\s*[)）]$")
SECTION_HEAD = re.compile(r"^(第.{1,4}[章节编辑]|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[)）])")
ISSUE_HEAD = re.compile(r"^第\s*(\d+)\s*辑$")


def rows(lines: list[dict]) -> list[str]:
    """Group OCR line boxes into reading-order rows by vertical overlap."""
    if not lines:
        return []
    items = []
    for ln in lines:
        ys = [pt[1] for pt in ln["box"]]
        xs = [pt[0] for pt in ln["box"]]
        items.append((min(ys), max(ys), min(xs), ln["text"]))
    tol = median(b - t for t, b, _, _ in items) * 0.5
    items.sort(key=lambda it: (it[0] + it[1]) / 2)

    grouped, cur, cur_y = [], [], None
    for it in items:
        yc = (it[0] + it[1]) / 2
        if cur and abs(yc - cur_y) > tol:
            grouped.append(cur)
            cur = []
        cur.append(it)
        cur_y = sum((c[0] + c[1]) / 2 for c in cur) / len(cur)
    grouped.append(cur)
    return [" ".join(t for *_, t in sorted(g, key=lambda it: it[2])).strip() for g in grouped]


def fonds(page_rows: list[str]) -> list[dict]:
    out = []
    for r in page_rows:
        if m := FONDS_ROW.match(r):
            out.append({"fonds_no": m["no"], "name": m["name"].strip(),
                        "counts": [int(c) for c in m["counts"].split()],
                        "date_start": int(m["start"]), "date_end": int(m["end"])})
    return out


# Imprint lines (出版：…, 编　辑：…, 主办, 印刷, any "label：value") mention 文史 but are not series headings.
IMPRINT_LINE = re.compile(r"(出\s*版|编\s*辑|主\s*办|印\s*刷|[：:])")


def is_series_head(r: str) -> bool:
    return "文史" in r and len(r) < 30 and not re.search(r"[…⋯]", r) and not IMPRINT_LINE.search(r)


def merge_wrapped(page_rows: list[str], row_re: re.Pattern, standalone=SECTION_HEAD.match) -> list[str]:
    """Join a row that doesn't match onto the next one when it is a wrapped entry."""
    out, carry = [], ""
    for r in page_rows:
        if standalone(r):
            out += [carry, r] if carry else [r]
            carry = ""
        elif row_re.match(r):
            out.append(carry + r)
            carry = ""
        else:
            if carry:
                out.append(carry)
            carry = r
    if carry:
        out.append(carry)
    return out


def toc(page_rows: list[str]) -> list[dict]:
    """Contents entries of a handbook; in fonds guides these are mostly fonds names."""
    out, section = [], None
    for r in merge_wrapped(page_rows, TOC_ROW):
        if SECTION_HEAD.match(r) and not TOC_ROW.match(r):
            section = r.strip()
        elif not FONDS_ROW.match(r) and (m := TOC_ROW.match(r)):
            heading = re.sub(rf"{LEADER}+$", "", m["heading"]).strip()
            if heading:
                out.append({"heading": heading, "section": section, "page": int(m["page"])})
    return out


def articles(page_rows: list[str], ctx: dict, headings: bool = True) -> list[dict]:
    """ctx carries the current series/issue heading across pages of a volume. With headings=False
    (fallback over lines the model did not mark up), entries are read but series/issue are left alone."""
    out = []
    standalone = lambda r: bool(SECTION_HEAD.match(r)) or is_series_head(r)
    for r in merge_wrapped(page_rows, ARTICLE_ROW, standalone):
        if headings and (m := ISSUE_HEAD.match(r.replace(" ", ""))):
            ctx["issue"] = int(m.group(1))
        elif m := ARTICLE_ROW.match(r):
            out.append({"series": ctx.get("series"), "issue": ctx.get("issue"),
                        "title": m["title"].strip(), "author": m["author"].strip() or None,
                        "page": int(m["page"])})
        elif headings and is_series_head(r):
            ctx["series"], ctx["issue"] = r.strip(), None
    return out
