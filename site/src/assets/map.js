// Map of provinces (shaded by number of guides) with prefecture and county archives on top.
// Province popup: count + link to the province's guide list. Prefecture/county popup: its guides.
const el = document.getElementById("map");
const base = el.dataset.base.replace(/\/$/, "");
const css = getComputedStyle(document.documentElement);
const RAMP = [0, 1, 2, 3, 4].map((i) => css.getPropertyValue(`--seq-${i}`).trim()); // home-page ramp
const PLACE = { prefecture: "#0f766e", county: "#c2410c" };

const map = new maplibregl.Map({
  container: el,
  style: "https://tiles.openfreemap.org/styles/positron",
  center: [105, 34],
  zoom: 3.3,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

const escape = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const href = (h) => (h.startsWith("/") ? base + h : h);
// Same link policy as the site build (eleventy.config.js): outside links open in a new tab, marked.
const NEW_TAB = ' target="_blank" rel="noopener"';
const NEW_TAB_LABEL = '<span class="ext" aria-hidden="true">↗</span>' +
  '<span class="visually-hidden"> (opens in a new tab, <span lang="zh">在新标签页打开</span>)</span>';
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

function popupHtml(p) {
  const head = `<strong lang="zh">${escape(p.name_zh)}</strong> <small>${escape(p.name_pinyin)} · ${escape(p.level)}</small>`;
  if (p.level === "province") {
    return `${head}<p>${plural(p.count, "guide")} in this province</p>` +
      `<p><a href="${escape(href(p.browse))}">View all ${plural(p.count, "guide")} →</a></p>`;
  }
  const guides = typeof p.guides === "string" ? JSON.parse(p.guides) : p.guides;
  const items = guides.map((g) =>
    `<li><a href="${escape(href(g.href))}"${g.local ? "" : NEW_TAB}>${escape(g.title)}${g.local ? "" : NEW_TAB_LABEL}</a> ${g.year ?? ""}${g.local ? "" : " <small>(catalog)</small>"}</li>`).join("");
  const more = p.browse && p.province_count
    ? `<p><a href="${escape(href(p.browse))}">All ${plural(p.province_count, "guide")} in <span lang="zh">${escape(p.province_zh)}</span> →</a></p>` : "";
  return `${head}<ul>${items}</ul>${more}`;
}

map.on("load", async () => {
  const data = await (await fetch(el.dataset.geojson)).json();
  map.addSource("places", { type: "geojson", data, promoteId: "id" });

  const isLevel = (level) => ["==", ["get", "level"], level];
  map.addLayer({
    id: "provinces", type: "fill", source: "places", filter: isLevel("province"),
    paint: { "fill-color": ["match", ["get", "cls"], 0, RAMP[0], 1, RAMP[1], 2, RAMP[2], 3, RAMP[3], RAMP[4]],
      "fill-opacity": 0.55 },
  });
  map.addLayer({ id: "province-lines", type: "line", source: "places", filter: isLevel("province"),
    paint: { "line-color": "#ffffff", "line-width": 1 } });
  for (const level of ["prefecture", "county"]) {
    map.addLayer({ id: `${level}-fill`, type: "fill", source: "places", filter: isLevel(level),
      paint: { "fill-color": PLACE[level], "fill-opacity": 0.25 } });
    map.addLayer({ id: `${level}-line`, type: "line", source: "places", filter: isLevel(level),
      paint: { "line-color": PLACE[level], "line-width": 1 } });
  }

  const popup = (f, lngLat) =>
    new maplibregl.Popup({ maxWidth: "320px" }).setLngLat(lngLat).setHTML(popupHtml(f.properties)).addTo(map);

  const clickable = ["county-fill", "prefecture-fill", "provinces"]; // smallest unit under the cursor wins
  map.on("click", (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: clickable });
    const f = clickable.map((l) => hits.find((h) => h.layer.id === l)).find(Boolean);
    if (f) popup(f, e.lngLat);
  });
  for (const l of clickable) {
    map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
  }

  // Zoom to a place given in the URL hash (e.g. #county:510108, #province:32), else to all provinces.
  const target = decodeURIComponent(location.hash.slice(1));
  const focus = data.features.filter((f) => (target ? f.properties.id === target : f.properties.level === "province"));
  if (focus.length) {
    const b = new maplibregl.LngLatBounds();
    for (const f of focus) {
      const rings = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
      rings.flat(2).forEach((c) => b.extend(c));
    }
    map.fitBounds(b, { padding: 40, maxZoom: 8, duration: 0 });
    if (target) popup(focus[0], b.getCenter());
  }
});
