import { readFileSync } from "node:fs";

// Home-page map data written by the pipeline (`uv run cah export`).
const path = new URL("../../../data/export/site/provinces.json", import.meta.url);

export default function () {
  return JSON.parse(readFileSync(path, "utf-8"));
}
