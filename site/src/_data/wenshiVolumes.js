import guides from "./guides.js";

// One entry per wenshi ziliao locality volume (the unit for pages and search ranking).
export default async function () {
  return (await guides())
    .filter((g) => g.kind === "wenshi")
    .flatMap((g) => g.volumes.map((v) => {
      return {
        ...v,
        item: { id: g.id, title: g.title_zh, short: (g.title_zh || "").split(". ").pop(), year: g.year,
                province: g.province, urls: g.urls, rights: g.rights, volumesTotal: g.volumes_total },
        // Only counts and series names are shown on the page (the scans carry the entries).
        articles: { length: g.articles.filter((a) => a.volume_id === v.id).length },
        series: [...new Set(g.articles.filter((a) => a.volume_id === v.id && a.series).map((a) => a.series))].slice(0, 5),
      };
    }));
}
