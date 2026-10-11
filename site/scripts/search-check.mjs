// Query the built Pagefind index from Node, the same way the browser does.
// Usage: serve site/_site on a port, then `node scripts/search-check.mjs [baseUrl]`.
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = process.argv[2] || "http://127.0.0.1:8765/";
const queries = [
  ["四川省粮食", "simplified Chinese"],
  ["四川省糧食", "traditional Chinese (variant folding)"],
  ["邮政", "simplified, short word"],
  ["郵政", "traditional, short word"],
  ["煤矿", "wenshi ziliao subject"],
  ["Hunan", "pinyin/English title"],
  ["dang an guan", "romanized title words"],
  ["交通部公路总局", "multi-word institution name, no spaces"],
  ["八九", "text from a page DashScope refused (RapidOCR fallback)"],
  ["卢焘", "wenshi ziliao person name"],
];

// pagefind.js resolves its index relative to its own URL; load it from a temp copy
// and point it at the served bundle.
const src = await (await fetch(new URL("pagefind/pagefind.js", base))).text();
const file = join(mkdtempSync(join(tmpdir(), "pf-")), "pagefind.mjs");
writeFileSync(file, src);
const pagefind = await import(file);
await pagefind.options({ basePath: new URL("pagefind/", base).href });
await pagefind.init();

console.log("Filters:", JSON.stringify(await pagefind.filters()));
for (const [q, note] of queries) {
  const r = await pagefind.search(q);
  const top = await Promise.all(r.results.slice(0, 3).map((x) => x.data()));
  console.log(`\n${q}  (${note}) -> ${r.results.length} result(s)`);
  for (const d of top) {
    const sub = d.sub_results?.[0];
    console.log(`  ${d.meta.title}  ${sub ? sub.url : d.url}`);
    console.log(`    ${(sub?.excerpt || d.excerpt).replace(/<\/?mark>/g, "*").slice(0, 90)}`);
  }
}

const filtered = await pagefind.search("学校", { filters: { Type: "Wenshi ziliao index 文史资料" } });
console.log(`\n学校 filtered to Type=wenshi -> ${filtered.results.length} result(s)`);
const span = await pagefind.search(null, { filters: { "Fonds cover": "1940s" } });
console.log(`Fonds cover = 1940s -> ${span.results.length} guide(s)`);
