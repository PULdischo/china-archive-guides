import { readFileSync } from "node:fs";
import { FACET_SLUGS, valueSlug } from "../assets/browse-slugs.js";

// One pre-rendered page per single filter value (province, type, decade…), from the search index's
// unit table, so browsing needs no search and no JavaScript.
const units = JSON.parse(readFileSync(new URL("../../../data/export/csi/units.json", import.meta.url), "utf-8")).units;
const provinces = JSON.parse(readFileSync(new URL("../../../data/export/site/provinces.json", import.meta.url), "utf-8")).provinces;
const FACET_LABELS = { Province: "Province", Type: "Type", Published: "Published", "Fonds cover": "Fonds cover" };
const isWenshi = (u) => u.href.startsWith("/wenshi/");
const newest = (a, b) => (b.year || "").localeCompare(a.year || "") || a.title.localeCompare(b.title, "zh");

export default function () {
  const pages = new Map();
  for (const u of units) {
    for (const [facet, values] of Object.entries(u.filters)) {
      if (!FACET_SLUGS[facet]) continue;
      for (const value of values) {
        const key = `${facet}\u0000${value}`;
        if (!pages.has(key)) pages.set(key, { facet, value, facetSlug: FACET_SLUGS[facet], valueSlug: valueSlug(value),
                                              facetLabel: FACET_LABELS[facet], units: [] });
        pages.get(key).units.push(u);
      }
    }
  }
  return [...pages.values()].map((p) => {
    const prov = p.facet === "Province" ? provinces.find((x) => x.zh === p.value) : null;
    // Wenshi volumes grouped under their provincial index; counts are guides (handbooks + indexes).
    const indexes = new Map();
    for (const u of p.units.filter(isWenshi)) {
      if (!indexes.has(u.item)) indexes.set(u.item, { id: u.item, title: u.item_title, year: u.year, volumes: [] });
      indexes.get(u.item).volumes.push(u);
    }
    for (const ix of indexes.values()) ix.volumes.sort((a, b) => a.title.localeCompare(b.title, "zh"));
    const handbooks = p.units.filter((u) => !isWenshi(u)).sort(newest);
    return { ...p, label: prov && prov.en ? `${p.value} ${prov.en}` : p.value, mapId: prov?.d ? `province:${prov.code}` : null,
             handbooks, indexes: [...indexes.values()].sort((a, b) => a.title.localeCompare(b.title, "zh")),
             nVolumes: p.units.filter(isWenshi).length };
  });
}
