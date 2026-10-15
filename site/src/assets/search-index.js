// Query side of the static search index (pipeline/src/cah/csindex.py): intersect postings, filter
// and count at the level of units (handbooks, wenshi volumes), verify exact strings on the pages
// shown. Cost does not grow with how many pages match.
import { makeNormalize, queryTerms, encode } from "./tokenize.js";

const fnv1a = (s) => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0;
  return h >>> 0;
};

export async function openIndex(base, fetchFn = fetch) {
  const url = (p) => new URL(p, base).href;
  const [meta, docsBuf, t2s] = await Promise.all([
    fetchFn(url("units.json")).then((r) => r.json()),
    fetchFn(url("docs.bin")).then((r) => r.arrayBuffer()),
    fetchFn(url("t2s.json")).then((r) => r.json()),
  ]);
  const dv = new DataView(docsBuf);
  const docUnit = new Uint16Array(meta.docs), docPage = new Uint16Array(meta.docs);
  for (let i = 0; i < meta.docs; i++) { docUnit[i] = dv.getUint16(i * 4, true); docPage[i] = dv.getUint16(i * 4 + 2, true); }
  const shardCache = new Map(), docCache = new Map();
  return {
    units: meta.units, docUnit, docPage, normalize: makeNormalize(t2s),
    async postings(token) {
      const s = fnv1a(token) % meta.shards;
      if (!shardCache.has(s)) shardCache.set(s, fetchFn(url(`p/${s}.bin`)).then((r) => r.arrayBuffer()));
      const b = new Uint8Array(await shardCache.get(s));
      const v = () => { let x = 0, shift = 0, c; do { c = b[pos++]; x += (c & 0x7f) * 2 ** shift; shift += 7; } while (c & 0x80); return x; };
      let pos = 4;
      const count = new DataView(b.buffer).getUint32(0, true);
      for (let t = 0; t < count; t++) {
        const len = b[pos++];
        let tok = "";
        for (let i = 0; i < len; i++) tok += String.fromCharCode(b[pos + i]);
        pos += len;
        const n = v();
        if (tok === token) {
          const out = new Uint32Array(n);
          let d = -1;
          for (let i = 0; i < n; i++) { d += v() + 1; out[i] = d; }
          return out;
        }
        for (let i = 0; i < n; i++) v(); // skip this token's postings
      }
      return new Uint32Array(0);
    },
    doc(id) {
      if (!docCache.has(id)) docCache.set(id, fetchFn(url(`d/${id}.json`)).then((r) => r.json()));
      return docCache.get(id);
    },
  };
}

function intersect(lists) {
  lists.sort((a, b) => a.length - b.length);
  let cur = lists[0];
  for (let k = 1; k < lists.length && cur.length; k++) {
    const other = lists[k], out = [];
    let j = 0;
    for (let i = 0; i < cur.length; i++) {
      while (j < other.length && other[j] < cur[i]) j++;
      if (j < other.length && other[j] === cur[i]) out.push(cur[i]);
    }
    cur = out;
  }
  return Array.from(cur);
}

const unitPasses = (u, filters) =>
  Object.entries(filters).every(([k, v]) => (u.filters[k] || []).includes(v));

export async function search(ix, query, { filters = {}, offset = 0, want = 10, pagesPerGuide = 3 } = {}) {
  const nq = ix.normalize(query).trim();
  const phrases = nq.split(/\s+/).filter(Boolean);
  const terms = queryTerms(nq);
  if (!terms.length) return { guides: [], totalGuides: 0, totalPages: 0 };
  const lists = await Promise.all(terms.map((t) => ix.postings(encode(t))));
  const hits = intersect(lists).filter((d) => unitPasses(ix.units[ix.docUnit[d]], filters));

  // Count matching documents per unit; rank units by count (then newest first).
  const byUnit = new Map();
  for (const d of hits) { const u = ix.docUnit[d]; if (!byUnit.has(u)) byUnit.set(u, []); byUnit.get(u).push(d); }
  const ranked = [...byUnit.entries()].sort((a, b) => b[1].length - a[1].length || (ix.units[b[0]].year || "").localeCompare(ix.units[a[0]].year || ""));

  // Exact-string check on the displayed pages (bigrams can co-occur without being adjacent).
  const latin = (p) => /^[a-z0-9]+$/.test(p);
  const required = phrases.every(latin) ? [phrases.join(" ")] : phrases;
  const exactByIndex = phrases.length === 1 && Array.from(phrases[0]).length <= 2 && !latin(phrases[0]);
  const has = (text, p) => (/^[a-z0-9 ]+$/.test(p)
    ? new RegExp(`(^|[^a-z0-9])${p.split(" ").join("\\s+")}($|[^a-z0-9])`).test(text) : text.includes(p));

  const guides = [];
  let next = offset;
  while (guides.length < want && next < ranked.length) {
    const batch = ranked.slice(next, next + (want - guides.length));
    next += batch.length;
    const found = await Promise.all(batch.map(async ([u, docs]) => {
      const pages = [];
      for (let i = 0; i < docs.length && pages.length < pagesPerGuide; i += pagesPerGuide * 2) {
        const data = await Promise.all(docs.slice(i, i + pagesPerGuide * 2).map(async (d) => ({ d, ...(await ix.doc(d)) })));
        for (const x of data) {
          if (pages.length >= pagesPerGuide) break;
          if (exactByIndex || required.every((p) => has(ix.normalize(x.t), p))) pages.push(x);
        }
      }
      if (!pages.length) return null;
      const unit = ix.units[u];
      return {
        guide: unit.id, href: unit.href, title: unit.title, year: unit.year, matches: docs.length,
        pages: pages.map((x) => ({
          url: ix.docPage[x.d] ? `${unit.href}#p${ix.docPage[x.d]}` : unit.href,
          meta: { page: x.p, text: x.t, viewer: x.v, kind: ix.docPage[x.d] ? "page" : "guide", href: unit.href },
        })),
      };
    }));
    guides.push(...found.filter(Boolean));
  }
  return { guides, totalGuides: ranked.length, totalPages: hits.length, next };
}

// Browse by filters with no query: units, newest first.
export function browse(ix, filters, { offset = 0, want = 20 } = {}) {
  const units = ix.units.filter((u) => unitPasses(u, filters)).sort((a, b) => (b.year || "").localeCompare(a.year || ""));
  return {
    guides: units.slice(offset, offset + want).map((u) => ({ guide: u.id, href: u.href, title: u.title, year: u.year, matches: null, pages: [] })),
    totalGuides: units.length, next: offset + want, browse: true,
  };
}
