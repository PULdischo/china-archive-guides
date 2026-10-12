import guides from "./guides.js";

// One entry per wenshi ziliao locality volume (the unit for pages and search ranking).
export default function () {
  return guides()
    .filter((g) => g.kind === "wenshi")
    .flatMap((g) => g.volumes.map((v) => ({
      ...v,
      item: { id: g.id, title: g.title_zh, short: (g.title_zh || "").split(". ").pop(), year: g.year,
              province: g.province, urls: g.urls, rights: g.rights, volumesTotal: g.volumes_total },
      articles: g.articles.filter((a) => a.volume_id === v.id),
    })));
}
