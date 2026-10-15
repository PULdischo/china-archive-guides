import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";

// Site-ready guides written by the pipeline (`uv run cah export`), one JSON object per line:
// the whole set is too large for a single JSON string in Node.
const path = new URL("../../../data/export/site/guides.jsonl", import.meta.url);

let cache;
export default async function () {
  if (!cache) {
    cache = [];
    for await (const line of createInterface({ input: createReadStream(path), crlfDelay: Infinity })) {
      if (line.trim()) cache.push(JSON.parse(line));
    }
  }
  return cache;
}
