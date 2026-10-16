// URL slugs for pre-rendered browse pages (/browse/{facet}/{value}/). Shared by the site build
// (eleventy.config.js, _data/browse.js) and the search page, which forwards a blank query with a
// single filter to the matching static page.
export const FACET_SLUGS = { Province: "province", Type: "type", Published: "published", "Fonds cover": "fonds-cover" };
const VALUE_SLUGS = {
  "Archival handbook 档案馆指南": "handbook",
  "Wenshi ziliao index 文史资料": "wenshi",
  "中央/全国": "national",
  "Before 1900": "before-1900",
};
export const valueSlug = (value) => VALUE_SLUGS[value] ?? value;
export const browsePath = (facet, value) =>
  FACET_SLUGS[facet] ? `browse/${FACET_SLUGS[facet]}/${encodeURIComponent(valueSlug(value))}/` : null;
