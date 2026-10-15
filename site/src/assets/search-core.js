// Query side of the search index built by `cah search` (pipeline/src/cah/search.py).
// Must normalize, tokenize and encode exactly like the Python side.
const HAN = "\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff";
const HAN_CHAR = new RegExp(`[${HAN}]`, "u");
const RUNS = new RegExp(`[${HAN}]+|[a-z0-9]+`, "gu");
const SPACE_BETWEEN_HAN = new RegExp(`(?<=[${HAN}])\\s+(?=[${HAN}])`, "gu");

export function makeNormalize(t2s) {
  return (text) =>
    Array.from(text || "", (c) => t2s[c] ?? c).join("").toLowerCase().replace(SPACE_BETWEEN_HAN, "");
}

export function tokens(normalized) {
  const out = [];
  for (const run of normalized.match(RUNS) || []) {
    if (HAN_CHAR.test(run)) {
      const chars = Array.from(run);
      out.push(...chars);
      for (let i = 0; i < chars.length - 1; i++) out.push(chars[i] + chars[i + 1]);
    } else out.push(run);
  }
  return out;
}

// Han token -> opaque ASCII word that Pagefind's segmenter and stemmer leave alone.
export const encode = (t) => (HAN_CHAR.test(t) ? "h" + Array.from(t, (c) => c.codePointAt(0)).join("x") : t);

// Query terms: Han runs as bigrams (a single character stays a unigram), other words as-is.
export function queryTerms(normalized) {
  const out = [];
  for (const run of normalized.match(RUNS) || []) {
    const chars = Array.from(run);
    if (HAN_CHAR.test(run) && chars.length > 1) {
      for (let i = 0; i < chars.length - 1; i++) out.push(chars[i] + chars[i + 1]);
    } else out.push(run);
  }
  return [...new Set(out)];
}

// Search in two steps:
// 1. One Pagefind search gives, via the per-guide filter, how many pages of each guide match
//    (no fragments loaded). Guides are ranked by that count.
// 2. For the top guides, a filtered search loads their best pages, which are checked for the
//    exact query string (bigrams can co-occur without being adjacent).
export async function search(pagefind, normalize, query,
  { filters = {}, offset = 0, want = 10, pagesPerGuide = 3 } = {}) {
  const nq = normalize(query).trim();
  const phrases = nq.split(/\s+/).filter(Boolean); // each whitespace-separated part must occur verbatim
  const terms = queryTerms(nq);
  if (!terms.length) return { guides: [], totalGuides: 0, totalPages: 0 };
  const q = terms.map(encode).join(" ");
  await pagefind.filters(); // per-filter counts are only computed once the filter index is loaded
  const r = await pagefind.search(q, { filters });
  const counts = Object.entries(r.filters?.guide || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  // A 1-2 character Han query is itself an indexed token, so every hit is already exact.
  const exactByIndex = phrases.length === 1 && Array.from(phrases[0]).length <= 2 && HAN_CHAR.test(phrases[0]);
  // Han strings match as substrings; romanized/English words must match whole words
  // ("xian" should not match "xiang").
  const latin = (p) => /^[a-z0-9]+$/.test(p);
  const wordRe = (p) => new RegExp(`(^|[^a-z0-9])${p}($|[^a-z0-9])`);
  const contains = (text, p) => (latin(p) ? wordRe(p).test(text) : text.includes(p));
  // A query of romanized words only ("Shanghai Shi") is a phrase; Chinese terms separated by
  // spaces must each occur, in any order.
  const required = phrases.every(latin) ? [phrases.join(" ")] : phrases;
  const contains2 = (text, p) => (p.includes(" ") ? new RegExp(`(^|[^a-z0-9])${p.split(" ").join("\\s+")}($|[^a-z0-9])`).test(text) : contains(text, p));
  const verify = (d) => {
    if (exactByIndex) return true;
    const text = normalize(d.meta.text);
    return required.every((p) => contains2(text, p));
  };

  // One search restricted to the next batch of top units (any of them), instead of one search per unit:
  // each Pagefind search recomputes counts for ~1,600 filter values, so 10 searches cost ~1 s.
  const guides = [];
  let next = offset;
  while (guides.length < want && next < counts.length) {
    const batch = counts.slice(next, next + (want - guides.length));
    next += batch.length;
    const ids = batch.map(([id]) => id);
    const gr = await pagefind.search(q, { filters: { ...filters, guide: { any: ids } } });
    const pages = new Map(ids.map((id) => [id, []]));
    // Fragments in score order, in batches, until every unit has its pages or results run out.
    const cap = Math.min(gr.results.length, ids.length * pagesPerGuide * 4);
    for (let i = 0; i < cap && [...pages.values()].some((ps) => ps.length < pagesPerGuide); i += 24) {
      const data = await Promise.all(gr.results.slice(i, Math.min(i + 24, cap)).map((x) => x.data()));
      for (const d of data) {
        const ps = pages.get(d.meta.guide);
        if (ps && ps.length < pagesPerGuide && verify(d)) ps.push(d);
      }
    }
    // A unit with many matching pages can crowd the others out of the loaded window; give any
    // unit still without pages its own (rare, cheap) search.
    await Promise.all(ids.filter((id) => !pages.get(id).length).map(async (id) => {
      const own = await pagefind.search(q, { filters: { ...filters, guide: id } });
      const data = await Promise.all(own.results.slice(0, pagesPerGuide * 2).map((x) => x.data()));
      pages.set(id, data.filter(verify).slice(0, pagesPerGuide));
    }));
    for (const [id, n] of batch) {
      const ps = pages.get(id);
      if (ps.length) guides.push({ guide: id, href: ps[0].meta.href, title: ps[0].meta.title, year: ps[0].meta.year, matches: n, pages: ps });
    }
  }
  return { guides, totalGuides: counts.length, totalPages: r.results.length, next };
}

// Browse by filters with no query: guide records only, newest first.
export async function browse(pagefind, filters, { offset = 0, want = 20 } = {}) {
  const r = await pagefind.search(null, { filters: { ...filters, record: "guide" }, sort: { year: "desc" } });
  const data = await Promise.all(r.results.slice(offset, offset + want).map((x) => x.data()));
  return {
    guides: data.map((d) => ({ guide: d.meta.guide, href: d.meta.href, title: d.meta.title, year: d.meta.year, matches: null, pages: [] })),
    totalGuides: r.results.length, next: offset + data.length, browse: true,
  };
}
