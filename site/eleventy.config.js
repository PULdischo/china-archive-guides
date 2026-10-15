import { HtmlBasePlugin } from "@11ty/eleventy";

export default function (eleventyConfig) {
  // GitHub Pages project sites live under /<repo>/; CI sets PATH_PREFIX.
  eleventyConfig.addPlugin(HtmlBasePlugin);

  eleventyConfig.addPassthroughCopy({ "src/assets": "assets" });
  eleventyConfig.addPassthroughCopy({ "../data/export/site/places.geojson": "data/places.geojson" });
  // Custom static search index (pipeline: `cah csindex`).
  eleventyConfig.addPassthroughCopy({ "../data/export/csi": "csi" });
  // Self-hosted MapLibre (no third-party CDN; see PLAN.md §5 on access from China).
  eleventyConfig.addPassthroughCopy({
    "node_modules/maplibre-gl/dist/maplibre-gl.js": "assets/vendor/maplibre-gl.js",
    "node_modules/maplibre-gl/dist/maplibre-gl.css": "assets/vendor/maplibre-gl.css",
  });

  // Link policy: originals and other outside sites open in a new tab (researchers keep the
  // transcription open beside the scan; also the norm on Chinese sites), marked visibly and for
  // screen readers (WCAG G201). Links within the site stay in the same tab so Back keeps
  // search state. Internal links are root-relative, so an absolute http(s) URL is external.
  // assets/map.js applies the same policy to links it builds.
  const EXTERNAL_LINK = /<a\b([^>]*?)\bhref="(https?:\/\/[^"]+)"([^>]*)>([\s\S]*?)<\/a>/g;
  const NEW_TAB_TEXT = '<span class="visually-hidden"> (opens in a new tab, <span lang="zh">在新标签页打开</span>)</span>';
  const NEW_TAB_ICON = '<span class="ext" aria-hidden="true">↗</span>';
  eleventyConfig.addTransform("external-links", function (content) {
    if (!(this.page.outputPath || "").endsWith(".html")) return content;
    return content.replace(EXTERNAL_LINK, (match, pre, href, post, inner) => {
      if (/\btarget=/.test(pre + post)) return match;
      const textLink = inner.replace(/<[^>]+>/g, "").trim() !== ""; // no icon on image-only links
      return `<a${pre}href="${href}"${post} target="_blank" rel="noopener">${inner}` +
        `${textLink ? NEW_TAB_ICON : ""}${NEW_TAB_TEXT}</a>`;
    });
  });

  eleventyConfig.addFilter("decade", (year) => (year ? `${Math.floor(year / 10) * 10}s` : null));
  eleventyConfig.addFilter("iiif", (service, size = "!400,600") => `${service}/full/${size}/0/default.jpg`);
  eleventyConfig.addFilter("json", (v) => JSON.stringify(v));
  eleventyConfig.addFilter("pageCount", (g) => g.volumes.reduce((n, v) => n + v.pages.length, 0));
  // Nunjucks' selectattr only tests truthiness (no "equalto"), so filter by value here.
  eleventyConfig.addFilter("whereEq", (items, key, value) => items.filter((x) => x[key] === value));
  // Viewer link for a guide's scan number (rows don't carry it, to keep site data small).
  const viewers = new WeakMap();
  eleventyConfig.addFilter("viewerFor", (g, n) => {
    if (!viewers.has(g)) viewers.set(g, new Map(g.volumes.flatMap((v) => v.pages.map((p) => [p.n, p.viewer_url]))));
    return viewers.get(g).get(n);
  });
  eleventyConfig.addFilter("countOf", (guides, key) => guides.reduce((n, g) => n + g[key].length, 0));

  return {
    dir: { input: "src", output: "_site" },
    pathPrefix: process.env.PATH_PREFIX || "/",
  };
}
