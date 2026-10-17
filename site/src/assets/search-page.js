// Search page over the static search index (search-index.js).
import * as engine from "./search-index.js";
import { browsePath } from "./browse-slugs.js";

const root = document.getElementById("search-app");
const base = root.dataset.base.replace(/\/?$/, "/");
const form = root.querySelector("form");
const input = form.querySelector("input[name=q]");
const filterBox = root.querySelector(".filters");
const status = root.querySelector(".status");
const list = root.querySelector(".results");
const more = root.querySelector(".more");
const FACETS = ["Type", "Province", "Published", "Fonds cover"];

// Same link policy as the site build (eleventy.config.js): outside links open in a new tab, marked.
const NEW_TAB = ' target="_blank" rel="noopener"';
const NEW_TAB_LABEL = '<span class="ext" aria-hidden="true">↗</span>' +
  '<span class="visually-hidden"> (opens in a new tab, <span lang="zh">在新标签页打开</span>)</span>';
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// A blank query with a single filter is a pre-rendered browse page: go there before loading the index.
{
  const params = new URLSearchParams(location.search);
  const filters = [...params.entries()].filter(([k, v]) => k !== "q" && v);
  const path = !(params.get("q") || "").trim() && filters.length === 1 && browsePath(...filters[0]);
  if (path) {
    location.replace(base + path);
    await new Promise(() => {}); // stop here while the browser navigates
  }
}

const ix = await engine.openIndex(new URL(`${base}csi/`, location.href));
const normalize = ix.normalize;
const t2s = await (await fetch(`${base}csi/t2s.json`)).json();
// Facet values with counts of guides (a handbook, or a wenshi provincial index however many volumes
// it has), matching the home page and browse pages.
const facetValues = {};
const seen = new Set();
for (const u of ix.units) for (const [k, vs] of Object.entries(u.filters)) for (const v of vs) {
  const key = `${k}\u0000${v}\u0000${u.item || u.id}`;
  if (seen.has(key)) continue;
  seen.add(key);
  (facetValues[k] ??= {})[v] = (facetValues[k][v] || 0) + 1;
}
const search = (q, opts) => engine.search(ix, q, opts);
const browse = (filters, opts) => engine.browse(ix, filters, opts);
const fold = (c) => (t2s[c] ?? c).toLowerCase();

// Facet selects with guide/volume counts.
for (const name of FACETS) {
  const values = Object.entries(facetValues[name] || {})
    .sort((a, b) => (b[0].startsWith("Before") - a[0].startsWith("Before")) || a[0].localeCompare(b[0], "zh"));
  if (!values.length) continue;
  filterBox.insertAdjacentHTML("beforeend",
    `<label>${esc(name)} <select name="${esc(name)}"><option value="">All</option>${values
      .map(([v, n]) => `<option value="${esc(v)}">${esc(v)} (${n})</option>`).join("")}</select></label>`);
}

// Excerpt around the first match of the query in the original text, highlighted.
function excerpt(text, query, width = 60) {
  const folded = Array.from(text, fold).join("");
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  const chars = Array.from(text);
  let at = -1, len = 0;
  for (const t of terms) {
    const i = folded.indexOf(t);
    if (i >= 0) { at = Array.from(folded.slice(0, i)).length; len = Array.from(t).length; break; }
  }
  if (at < 0) return esc(chars.slice(0, width * 2).join("")) + (chars.length > width * 2 ? " …" : "");
  const start = Math.max(0, at - width), end = Math.min(chars.length, at + len + width);
  return (start ? "… " : "") + esc(chars.slice(start, at).join("")) + "<mark>" +
    esc(chars.slice(at, at + len).join("")) + "</mark>" + esc(chars.slice(at + len, end).join("")) +
    (end < chars.length ? " …" : "");
}

let state = { q: "", filters: {}, offset: 0 };

function render(res, append) {
  if (!append) list.innerHTML = "";
  for (const g of res.guides) {
    const pages = g.pages.filter((p) => p.meta.kind === "page");
    const href = base + (g.href || `/guides/${g.guide}/`).replace(/^\//, "");
    list.insertAdjacentHTML("beforeend", `<li class="result">
      <h3><a href="${href}" lang="zh">${esc(g.title)}</a>${g.year ? ` <span class="year">${esc(g.year)}</span>` : ""}</h3>
      ${g.matches == null ? "" : `<p class="count">${g.matches} matching ${g.matches === 1 ? "record" : "records"}</p>`}
      <ul class="pages">${(pages.length ? pages : g.pages).map((p) => `<li>
        <a href="${esc(base + p.url.replace(/^\//, ""))}">${esc(p.meta.page || "Guide record")}</a>${p.meta.viewer
          ? `<a class="dpul" href="${esc(p.meta.viewer)}"${NEW_TAB}>DPUL${NEW_TAB_LABEL}</a>` : ""}
        <span class="excerpt" lang="zh">${excerpt(p.meta.text.replace(/\s+/g, " "), state.q)}</span></li>`).join("")}</ul>
    </li>`);
  }
  const where = Object.values(state.filters).join(", ");
  // Results are handbooks and individual wenshi volumes (an index has many), so say "results".
  const n = res.totalGuides, results = `${n} result${n === 1 ? "" : "s"}`;
  status.textContent = res.browse
    ? `${results} (handbooks and wenshi ziliao volumes)${where ? ` — ${where}` : ""}, newest first`
    : n
      ? `${results} for “${state.q}” (handbooks and wenshi ziliao volumes)` + (n > 1 ? ", ranked by number of matching pages" : "")
      : `No results for “${state.q}”.`;
  more.hidden = !(res.next < res.totalGuides);
  state.offset = res.next ?? 0;
}

async function run(append = false) {
  const filtered = Object.keys(state.filters).length > 0;
  if (!state.q.trim() && !filtered) { list.innerHTML = ""; status.textContent = ""; more.hidden = true; return; }
  status.textContent = "Searching…";
  const offset = append ? state.offset : 0;
  const res = state.q.trim()
    ? await search(state.q, { filters: state.filters, offset })
    : await browse(state.filters, { offset }); // combined filters; single ones are static browse pages
  render(res, append);
}

function readForm() {
  const filters = {};
  for (const sel of filterBox.querySelectorAll("select")) if (sel.value) filters[sel.name] = sel.value;
  state = { q: input.value.trim(), filters, offset: 0 };
  const params = new URLSearchParams({ q: state.q, ...filters });
  history.replaceState(null, "", `?${params}`);
}

form.addEventListener("submit", (e) => { e.preventDefault(); readForm(); run(); });
filterBox.addEventListener("change", () => { readForm(); run(); });
more.addEventListener("click", () => run(true));

// Restore from the URL (?q=…&Province=…).
const params = new URLSearchParams(location.search);
input.value = params.get("q") || "";
for (const sel of filterBox.querySelectorAll("select")) sel.value = params.get(sel.name) || "";
readForm();
run();
