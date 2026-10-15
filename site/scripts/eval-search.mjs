// Score the search index against data/export/search/eval.json (ground truth: units whose text
// contains the query string). Serve site/_site on a port first.
// Usage: node scripts/eval-search.mjs [baseUrl]
import { readFileSync, writeFileSync } from "node:fs";
import { openIndex, search } from "../src/assets/search-index.js";

const [base = "http://127.0.0.1:8766/"] = process.argv.slice(2);
const evalSet = JSON.parse(readFileSync("../data/export/search/eval.json", "utf-8"));
const ix = await openIndex(new URL("csi/", base));
const top10 = async (q) => (await search(ix, q)).guides.slice(0, 10).map((g) => g.guide);

const rows = [];
const t0 = performance.now();
for (const q of evalSet) {
  const s = performance.now();
  const got = await top10(q.q);
  const truth = new Set(q.truth);
  const hits = got.filter((g) => truth.has(g)).length;
  rows.push({
    type: q.type.replace("_traditional", " (trad.)"), q: q.q, truth: q.truth.length, returned: got.length,
    recall: hits / Math.min(10, truth.size), precision: got.length ? hits / got.length : 0,
    zero: got.length === 0, ms: performance.now() - s,
  });
}
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / (xs.length || 1);
const types = [...new Set(rows.map((r) => r.type))];
console.log(`queries=${rows.length}  total ${((performance.now() - t0) / 1000).toFixed(1)}s`);
console.log("type".padEnd(24), "n".padStart(3), "recall@10".padStart(10), "prec@10".padStart(8), "zero".padStart(5), "ms".padStart(6));
for (const t of [...types, "ALL"]) {
  const rs = t === "ALL" ? rows : rows.filter((r) => r.type === t);
  console.log(t.padEnd(24), String(rs.length).padStart(3), mean(rs.map((r) => r.recall)).toFixed(2).padStart(10),
    mean(rs.map((r) => r.precision)).toFixed(2).padStart(8), String(rs.filter((r) => r.zero).length).padStart(5),
    mean(rs.map((r) => r.ms)).toFixed(0).padStart(6));
}
writeFileSync(`../data/export/search/eval-results.json`, JSON.stringify(rows, null, 1));
