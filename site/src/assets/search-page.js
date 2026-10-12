// Search page: custom CJK tokens over the Pagefind index (see search-core.js).
import { makeNormalize, search } from "./search-core.js";

const root = document.getElementById("search-app");
const base = root.dataset.base.replace(/\/?$/, "/");
const form = root.querySelector("form");
const input = form.querySelector("input[name=q]");
const filterBox = root.querySelector(".filters");
const status = root.querySelector(".status");
const list = root.querySelector(".results");
const more = root.querySelector(".more");
const FACETS = ["Type", "Province", "Published", "Fonds cover"];

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const pagefind = await import(`${base}pagefind/pagefind.js`);
await pagefind.options({ baseUrl: base });
await pagefind.init();
const t2s = await (await fetch(`${base}pagefind/t2s.json`)).json();
const normalize = makeNormalize(t2s);
const fold = (c) => (t2s[c] ?? c).toLowerCase();

// Facet selects with (unfiltered) counts.
const all = await pagefind.filters();
for (const name of FACETS) {
  const values = Object.entries(all[name] || {}).sort((a, b) => a[0].localeCompare(b[0], "zh"));
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
    const href = `${base}guides/${g.guide}/`;
    list.insertAdjacentHTML("beforeend", `<li class="result">
      <h3><a href="${href}" lang="zh">${esc(g.title)}</a>${g.year ? ` <span class="year">${esc(g.year)}</span>` : ""}</h3>
      <p class="count">${g.matches} matching ${g.matches === 1 ? "record" : "records"}</p>
      <ul class="pages">${(pages.length ? pages : g.pages).map((p) => `<li>
        <a href="${esc(p.url)}">${esc(p.meta.page || "Guide record")}</a>
        <span class="excerpt" lang="zh">${excerpt(p.meta.text.replace(/\s+/g, " "), state.q)}</span></li>`).join("")}</ul>
    </li>`);
  }
  status.textContent = res.totalGuides
    ? `${res.totalGuides} guide${res.totalGuides === 1 ? "" : "s"} contain “${state.q}”` +
      (res.totalGuides > 1 ? " — ranked by number of matching pages" : "")
    : `No guides contain “${state.q}”.`;
  more.hidden = !(res.next < res.totalGuides);
  state.offset = res.next ?? 0;
}

async function run(append = false) {
  if (!state.q.trim()) { list.innerHTML = ""; status.textContent = ""; more.hidden = true; return; }
  status.textContent = "Searching…";
  const res = await search(pagefind, normalize, state.q, { filters: state.filters, offset: append ? state.offset : 0 });
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
