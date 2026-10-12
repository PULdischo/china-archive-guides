// Build the Pagefind index from pipeline search records (custom CJK tokens).
// Usage: node scripts/build-search-index.mjs [records.jsonl] [outputDir]
import { createReadStream, copyFileSync, rmSync } from "node:fs";
import { createInterface } from "node:readline";
import * as pagefind from "pagefind";

const recordsPath = process.argv[2] || "../data/export/search/records.jsonl";
const outputPath = process.argv[3] || "_site/pagefind";

// "en": our Han tokens are already ASCII words, so no CJK segmentation is wanted;
// English stemming only touches pinyin/English words, the same on both sides.
const { index, errors } = await pagefind.createIndex({ forceLanguage: "en" });
if (errors.length) throw new Error(errors.join("\n"));

let n = 0;
for await (const line of createInterface({ input: createReadStream(recordsPath) })) {
  if (!line.trim()) continue;
  const r = JSON.parse(line);
  const res = await index.addCustomRecord({
    url: r.url, content: r.content, language: "en", meta: r.meta, filters: r.filters, sort: r.sort,
  });
  if (res.errors.length) throw new Error(res.errors.join("\n"));
  n++;
}
rmSync(outputPath, { recursive: true, force: true }); // drop stale bundle files from earlier builds
const out = await index.writeFiles({ outputPath });
if (out.errors.length) throw new Error(out.errors.join("\n"));
copyFileSync("../data/export/search/t2s.json", `${outputPath}/t2s.json`);
console.log(`Indexed ${n} records -> ${outputPath}`);
await pagefind.close();
