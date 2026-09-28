// The tab's icons (public/favicon*, public/apple-touch-icon*, made by
// branding/build.py) keep their names from version to version, and browsers
// keep a favicon by its URL for a long time. So the page names each one with
// its content's hash (`/favicon.svg?v=<sha8>`): a new icon is a new URL, and
// shows after an update. The same hashed URLs go to the app, as
// `__BRAND_ICONS__`, for the practice profile's swap (src/app/brand.tsx).
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

export const PUBLIC_DIR = fileURLToPath(new URL("./public", import.meta.url));

export const BRAND_ICON_FILES = [
  "favicon.svg",
  "favicon-32.png",
  "apple-touch-icon.png",
  "favicon-practice.svg",
  "favicon-practice-32.png",
  "apple-touch-icon-practice.png",
] as const;

export function sha8(data: Buffer | string): string {
  return createHash("sha256").update(data).digest("hex").slice(0, 8);
}

/** "/favicon.svg" → "/favicon.svg?v=<the first 8 of its sha256>", for each icon. */
export function brandIconUrls(publicDir: string = PUBLIC_DIR): Record<string, string> {
  return Object.fromEntries(
    BRAND_ICON_FILES.map((name) => [`/${name}`, `/${name}?v=${sha8(readFileSync(join(publicDir, name)))}`]),
  );
}

/** index.html with each `data-brand` link's icon named by its hash. */
export function hashBrandLinks(html: string, urls: Record<string, string>): string {
  return html.replace(/<link\b[^>]*\bdata-brand\b[^>]*>/g, (link) =>
    link.replace(/\bhref="([^"?]+)(?:\?[^"]*)?"/, (_whole, path: string) => {
      const hashed = urls[path];
      if (!hashed) throw new Error(`index.html names a brand icon brandIcons.ts doesn't know: ${path}`);
      return `href="${hashed}"`;
    }),
  );
}

export function brandIcons(publicDir: string = PUBLIC_DIR): Plugin {
  const urls = () => brandIconUrls(publicDir);
  return {
    name: "datalab-brand-icons",
    config: () => ({ define: { __BRAND_ICONS__: JSON.stringify(urls()) } }),
    transformIndexHtml: (html) => hashBrandLinks(html, urls()),
  };
}
