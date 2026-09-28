// The tabs with resizable panels, and the Knowledge viewer, draw every surface
// with the theme's tokens: no white panel in dark mode.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { expect, it } from "vitest";

const SRC = join(__dirname, "..");
const files = (dir: string): string[] =>
  readdirSync(join(SRC, dir)).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(join(SRC, path)).isDirectory()) return files(path);
    return /\.tsx?$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });

const OWNED = [
  ...files("features/sql"),
  ...files("features/workflows"),
  ...files("features/pipelines"),
  ...files("features/knowledge"),
  ...files("components/layout"),
  "components/chat/Markdown.tsx",
  "components/ui/Loading.tsx",
];

it("has no white or hard-coded light backgrounds on these tabs", () => {
  const found = OWNED.flatMap((file) => {
    const text = readFileSync(join(SRC, file), "utf8");
    return [...text.matchAll(/\bbg-white\b|\bbg-\[#(?:fff|ffffff)\]|background(?:-color)?:\s*(?:#fff\b|#ffffff\b|white\b)/gi)].map(
      (m) => `${file}: ${m[0]}`,
    );
  });
  expect(found).toEqual([]);
});

it("scrolls wide tables and code blocks within their own blocks", () => {
  const css = readFileSync(join(SRC, "styles/index.css"), "utf8");
  const rule = (selector: string) => new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]*overflow-x:\\s*auto`);
  expect(css).toMatch(rule(".prose-datalab .dl-scroll-x"));
  expect(css).toMatch(rule(".prose-datalab pre"));
  // The prose's own surfaces are tokens.
  const prose = css.slice(css.indexOf(".prose-datalab {"), css.indexOf("input[type=\"checkbox\"]"));
  expect(prose).not.toMatch(/#fff|white\b/i);
});
