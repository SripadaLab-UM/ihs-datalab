// The colour tokens meet WCAG AA in both themes (from the design review: navigation
// and helper text read faint in the dark theme). Text: 4.5:1 on every surface it
// sits on. A field's edge: 3:1. Line is a decorative hairline, so it isn't held to it.
import { readFileSync } from "node:fs";

import { expect, it } from "vitest";

const css = readFileSync(`${__dirname}/index.css`, "utf8");

function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--color-([a-z-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
}
const light = tokens(css.split("@theme {")[1].split("}")[0]);
const dark = { ...light, ...tokens(css.split("prefers-color-scheme: dark")[1].split("{")[2].split("}")[0]) };

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const SURFACES = ["canvas", "surface", "rail", "field", "sunken", "accent-soft", "data-soft", "attn-soft", "you-soft", "danger-soft"];
const TEXT = ["ink", "muted", "faint", "data", "research", "attn", "you", "danger"];

it.each([
  ["light", light],
  ["dark", dark],
])("text and field edges meet AA in the %s theme", (_name, theme) => {
  for (const text of TEXT) {
    for (const surface of SURFACES) {
      expect(contrast(theme[text], theme[surface]), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
  }
  for (const surface of ["canvas", "field", "rail", "sunken"]) {
    expect(contrast(theme.edge, theme[surface]), `edge on ${surface}`).toBeGreaterThanOrEqual(3);
  }
  // The primary button's label.
  expect(contrast(theme["accent-ink"], theme.accent)).toBeGreaterThanOrEqual(4.5);
});

// Syntax colour in code blocks (components/code): 4.5:1 on every background code sits on,
// a diff's added and removed rows included.
const CODE = ["code-keyword", "code-string", "code-number", "code-comment", "code-function", "code-type"];
const CODE_BACKGROUNDS = ["sunken", "field", "surface", "data-soft", "danger-soft", "attn-soft"];

it.each([
  ["light", light],
  ["dark", dark],
])("code colours meet AA on code backgrounds in the %s theme", (_name, theme) => {
  for (const token of CODE) {
    expect(theme[token], token).toMatch(/^#[0-9a-f]{6}$/i);
    for (const background of CODE_BACKGROUNDS) {
      expect(contrast(theme[token], theme[background]), `${token} on ${background}`).toBeGreaterThanOrEqual(4.5);
    }
  }
  // Dark has its own values, not light's carried over.
  if (theme === dark) for (const token of CODE) expect(dark[token]).not.toBe(light[token]);
});

it("keeps small labels at a readable size", () => {
  const label = /\.dl-label\s*{[^}]*font-size:\s*([\d.]+)px/.exec(css);
  expect(Number(label?.[1])).toBeGreaterThanOrEqual(11.5);
});
