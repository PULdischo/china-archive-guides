import { existsSync, readFileSync } from "node:fs";

// Download list written by the pipeline's release step (`uv run cah release`): files, rows, sizes,
// checksums and the CSV data dictionary.
const path = new URL("../../../data/export/site/downloads.json", import.meta.url);

export default function () {
  if (!existsSync(path)) return null;
  const d = JSON.parse(readFileSync(path, "utf-8"));
  const base = `https://huggingface.co/datasets/${d.repo}/resolve/v${d.version}/`;
  const files = d.files.map((f) => ({ ...f, url: base + f.file.split("/").map(encodeURIComponent).join("/") }));
  const byFile = Object.fromEntries(files.map((f) => [f.file.replace(/^downloads\//, ""), f]));
  return { ...d, files, byFile, datasetUrl: `https://huggingface.co/datasets/${d.repo}`, manifestUrl: base + "downloads/manifest.json",
           provinces: files.filter((f) => f.group === "wenshi_by_province") };
}
