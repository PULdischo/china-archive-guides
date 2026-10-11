"""Sanitize model-produced HTML transcriptions and derive plain text from them.

Model output is untrusted: only a small allowlist of structural tags survives.
"""

import re
from html import escape
from html.parser import HTMLParser

import nh3

TAGS = {"h2", "h3", "h4", "p", "br", "table", "caption", "thead", "tbody", "tr", "th", "td",
        "ul", "ol", "li", "span"}
ATTRIBUTES = {"td": {"rowspan", "colspan"}, "th": {"rowspan", "colspan"}, "*": {"class"}}
# Elements that end a line of text; cells are separated by spaces.
BLOCKS = {"h2", "h3", "h4", "p", "br", "tr", "li", "caption", "table", "ul", "ol"}
CELLS = {"td", "th", "span"}


EMPTY_ROW = re.compile(r"<tr>(?:\s*<t[hd][^>]*>\s*</t[hd]>)+\s*</tr>")
EMPTY_SECTION = re.compile(r"<(thead|tbody)>\s*</\1>")


def sanitize(html: str) -> str:
    clean = nh3.clean(html or "", tags=TAGS, attributes=ATTRIBUTES, link_rel=None)
    # Rows with only empty cells are a model artifact (thin blank header rows).
    clean = EMPTY_SECTION.sub("", EMPTY_ROW.sub("", clean))
    return clean.strip()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lines, self.cur = [], []

    def _flush(self):
        line = " ".join("".join(self.cur).split())
        if line:
            self.lines.append(line)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKS:
            self._flush()
        elif tag in CELLS:
            self.cur.append(" ")

    def handle_endtag(self, tag):
        if tag in BLOCKS:
            self._flush()

    def handle_data(self, data):
        self.cur.append(data)


def to_lines(html: str) -> list[str]:
    """Reading-order text lines (one per paragraph, list item or table row)."""
    parser = _Text()
    parser.feed(html or "")
    parser.close()
    parser._flush()
    return parser.lines


def from_rows(toc: list[dict], fonds: list[dict], articles: list[dict]) -> str:
    """Rebuild a page's HTML from structured rows (when the model's HTML came back incomplete)."""
    e = lambda v: escape(str(v)) if v not in (None, "") else ""
    out = []
    if fonds:
        out.append("<table><thead><tr><th>全宗号</th><th>全宗名称</th><th>数量</th><th>起止年代</th></tr></thead><tbody>")
        for f in fonds:
            dates = "–".join(e(f.get(k)) for k in ("date_start", "date_end") if f.get(k))
            out.append(f"<tr><td>{e(f.get('fonds_no'))}</td><td>{e(f.get('name'))}</td>"
                       f"<td>{' / '.join(e(c) for c in f.get('counts') or [])}</td><td>{dates}</td></tr>")
        out.append("</tbody></table>")
    start = section = object()  # sentinel: no list opened yet
    items = [(t.get("section"), t.get("heading"), None, t.get("page")) for t in toc] + \
            [(a.get("series"), a.get("title"), a.get("author"), a.get("page")) for a in articles]
    for sec, text, author, page in items:
        if sec != section:
            if section is not start:
                out.append("</ul>")
            if sec:
                out.append(f"<h4>{e(sec)}</h4>")
            out.append('<ul class="toc">')
            section = sec
        au = f' <span class="au">{e(author)}</span>' if author else ""
        pg = f' <span class="pg">{e(page)}</span>' if page is not None else ""
        out.append(f"<li>{e(text)}{au}{pg}</li>")
    if items:
        out.append("</ul>")
    return sanitize("".join(out))
