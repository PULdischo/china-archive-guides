import { HtmlBasePlugin } from "@11ty/eleventy";

export default function (eleventyConfig) {
  // GitHub Pages project sites live under /<repo>/; CI sets PATH_PREFIX.
  eleventyConfig.addPlugin(HtmlBasePlugin);

  eleventyConfig.addPassthroughCopy({ "src/assets": "assets" });
  eleventyConfig.addPassthroughCopy({ "../data/export/site/places.geojson": "data/places.geojson" });
  // Self-hosted MapLibre (no third-party CDN; see PLAN.md §5 on access from China).
  eleventyConfig.addPassthroughCopy({
    "node_modules/maplibre-gl/dist/maplibre-gl.js": "assets/vendor/maplibre-gl.js",
    "node_modules/maplibre-gl/dist/maplibre-gl.css": "assets/vendor/maplibre-gl.css",
  });

  eleventyConfig.addFilter("decade", (year) => (year ? `${Math.floor(year / 10) * 10}s` : null));
  eleventyConfig.addFilter("iiif", (service, size = "!400,600") => `${service}/full/${size}/0/default.jpg`);
  eleventyConfig.addFilter("json", (v) => JSON.stringify(v));
  eleventyConfig.addFilter("pageCount", (g) => g.volumes.reduce((n, v) => n + v.pages.length, 0));
  eleventyConfig.addFilter("countOf", (guides, key) => guides.reduce((n, g) => n + g[key].length, 0));

  return {
    dir: { input: "src", output: "_site" },
    pathPrefix: process.env.PATH_PREFIX || "/",
  };
}
