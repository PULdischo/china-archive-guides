import { readFileSync } from "node:fs";

// Site-ready JSON written by the pipeline (`uv run cah export`).
const path = new URL("../../../data/export/site/guides.json", import.meta.url);

export default function () {
  return JSON.parse(readFileSync(path, "utf-8"));
}
