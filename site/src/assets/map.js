// Map of places from the legacy ArcGIS layers, with links to guides.
const el = document.getElementById("map");
const base = el.dataset.base.replace(/\/$/, "");
const COLORS = { national: "#7a4cc2", province: "#c2410c", prefecture: "#0f766e", county: "#1d4ed8" };

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

map.on("load", async () => {
  const data = await (await fetch(el.dataset.geojson)).json();
  // Draw larger units first so counties stay clickable on top.
  const order = ["national", "province", "prefecture", "county"];
  data.features.sort((a, b) => order.indexOf(a.properties.level) - order.indexOf(b.properties.level));
  map.addSource("places", { type: "geojson", data, promoteId: "id" });

  const color = ["match", ["get", "level"], ...Object.entries(COLORS).flat(), "#666"];
  map.addLayer({
    id: "places-fill", type: "fill", source: "places",
    // Larger units stay translucent so the counties inside them remain visible.
    paint: { "fill-color": color, "fill-opacity": ["case", ["get", "in_sample"],
      ["match", ["get", "level"], "county", 0.6, "prefecture", 0.4, 0.2], 0.08] },
  });
  map.addLayer({
    id: "places-line", type: "line", source: "places",
    paint: { "line-color": color, "line-width": ["case", ["get", "in_sample"], 1.5, 0.5] },
  });

  const popup = (f, lngLat) => {
    const p = f.properties;
    const guides = typeof p.guides === "string" ? JSON.parse(p.guides) : p.guides;
    const items = guides.map((g) =>
      `<li><a href="${escape(href(g.href))}"${g.local ? "" : NEW_TAB}>${escape(g.title)}${g.local ? "" : NEW_TAB_LABEL}</a> ${g.year ?? ""}${g.local ? "" : " <small>(catalog)</small>"}</li>`).join("");
    new maplibregl.Popup({ maxWidth: "320px" }).setLngLat(lngLat)
      .setHTML(`<strong lang="zh">${escape(p.name_zh)}</strong> <small>${escape(p.name_pinyin)} · ${escape(p.level)}</small><ul>${items}</ul>`)
      .addTo(map);
  };

  map.on("click", "places-fill", (e) => {
    // Prefer the smallest unit under the cursor (last drawn).
    popup(e.features[e.features.length - 1], e.lngLat);
  });
  map.on("mouseenter", "places-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "places-fill", () => (map.getCanvas().style.cursor = ""));

  // Zoom to the sample (or to a place given in the URL hash, e.g. #county:510108).
  const target = decodeURIComponent(location.hash.slice(1));
  const focus = data.features.filter((f) => (target ? f.properties.id === target : f.properties.in_sample));
  if (focus.length) {
    const b = new maplibregl.LngLatBounds();
    for (const f of focus) {
      const rings = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
      rings.flat(2).forEach((c) => b.extend(c));
    }
    map.fitBounds(b, { padding: 40, maxZoom: 8 });
    if (target) popup(focus[0], b.getCenter());
  }
});
