// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, it } from "vitest";

import { BRAND_ICON_FILES, brandIconUrls, hashBrandLinks, PUBLIC_DIR, sha8 } from "../../brandIcons";

const INDEX = fileURLToPath(new URL("../../index.html", import.meta.url));
const hashOf = (name: string) => sha8(readFileSync(join(PUBLIC_DIR, name)));

it("names every tab icon in index.html by its content's hash", () => {
  const html = hashBrandLinks(readFileSync(INDEX, "utf-8"), brandIconUrls());
  const hrefs = [...html.matchAll(/<link\b[^>]*\bdata-brand\b[^>]*>/g)].map((m) => /href="([^"]+)"/.exec(m[0])?.[1]);
  expect(hrefs).toEqual([
    `/favicon-32.png?v=${hashOf("favicon-32.png")}`,
    `/favicon.svg?v=${hashOf("favicon.svg")}`,
    `/apple-touch-icon.png?v=${hashOf("apple-touch-icon.png")}`,
  ]);
});

it("gives the app the same hashed URLs, practice's included", () => {
  expect(__BRAND_ICONS__).toEqual(brandIconUrls());
  for (const name of BRAND_ICON_FILES) expect(__BRAND_ICONS__[`/${name}`]).toBe(`/${name}?v=${hashOf(name)}`);
  // Each icon its own hash: real and practice never share a URL.
  expect(new Set(Object.values(__BRAND_ICONS__)).size).toBe(BRAND_ICON_FILES.length);
});

it("renames an icon again whenever it changes, and refuses one it doesn't know", () => {
  const html = '<link rel="icon" href="/favicon.svg?v=00000000" data-brand />';
  expect(hashBrandLinks(html, { "/favicon.svg": "/favicon.svg?v=12345678" })).toBe(
    '<link rel="icon" href="/favicon.svg?v=12345678" data-brand />',
  );
  expect(() => hashBrandLinks('<link href="/other.svg" data-brand />', {})).toThrow(/other\.svg/);
  // Links that aren't the brand's are left as they are.
  expect(hashBrandLinks('<link rel="stylesheet" href="/a.css" />', {})).toBe('<link rel="stylesheet" href="/a.css" />');
});
