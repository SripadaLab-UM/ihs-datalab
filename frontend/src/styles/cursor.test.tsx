// Cursor, hover and focus (docs/DESIGN.md): the global rules are in index.css's
// base layer, and the controls the app draws are ones those rules reach.
// Tailwind's preflight gives buttons the arrow, so without them nothing shows
// the hand.
import { readFileSync } from "node:fs";

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";

import { Button, InfoTip, Tabs } from "@/components/ui";

const css = readFileSync(`${__dirname}/index.css`, "utf8");

/** The base layer's rules, as selector → declarations (comments dropped). */
function baseRules(): Map<string, string> {
  const start = css.indexOf("@layer base {");
  expect(start, "index.css has a base layer").toBeGreaterThan(-1);
  // The layer's own braces: count to its end.
  let depth = 0;
  let end = start;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) {
      end = i;
      break;
    }
  }
  const body = css.slice(css.indexOf("{", start) + 1, end).replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = new Map<string, string>();
  for (const m of body.matchAll(/([^{}]+)\{([^{}]*)\}/g)) rules.set(m[1].replace(/\s+/g, " ").trim(), m[2]);
  return rules;
}

/** The selector of the base-layer rule that sets this cursor. */
function selectorFor(cursor: string): string {
  const found = [...baseRules()].filter(([, body]) => new RegExp(`cursor:\\s*${cursor}\\b`).test(body)).map(([sel]) => sel);
  expect(found.length, `a base rule sets cursor: ${cursor}`).toBeGreaterThan(0);
  return found.join(", ");
}

it("labels the block, so another branch's edits to index.css merge around it", () => {
  expect(css).toMatch(/\/\* Cursor, hover and focus: see docs\/DESIGN\.md/);
});

it("gives everything you can press the hand, at low specificity", () => {
  const pointer = selectorFor("pointer");
  expect(pointer.startsWith(":where(")).toBe(true);
  for (const part of ["button", "a[href]", "summary", "select", "label[for]", '[role="button"]', '[role="tab"]', '[role="menuitem"]', '[role="option"]', '[role="switch"]', '[role="checkbox"]', '[role="radio"]']) {
    expect(pointer, part).toContain(part);
  }
});

it("keeps the text cursor in fields and the editor, and not-allowed on disabled controls", () => {
  const text = selectorFor("text");
  for (const part of ["textarea", '[contenteditable="true"]', ".cm-content"]) expect(text, part).toContain(part);
  const disabled = selectorFor("not-allowed");
  expect(disabled).toContain(":disabled");
  expect(disabled).toContain('[aria-disabled="true"]');
});

// The fade keeps a disabled control's text readable: ink at 4.5:1, muted and faint at 3:1, on every surface.
function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--color-([a-z-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
}
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
function luminance([r, g, b]: number[]): number {
  const [R, G, B] = [r, g, b].map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}
function contrast(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

it.each(["light", "dark"])("fades disabled controls and keeps their text readable in the %s theme", (name) => {
  const light = tokens(css.split("@theme {")[1].split("}")[0]);
  const theme = name === "light" ? light : { ...light, ...tokens(css.split(':root[data-theme="dark"] {')[1].split("}")[0]) };
  const faded = [...baseRules()].find(([sel]) => sel.includes(":disabled"))?.[1] ?? "";
  const alpha = Number(/opacity:\s*([\d.]+)/.exec(faded)?.[1]);
  expect(alpha).toBeLessThan(1);
  for (const surface of ["canvas", "surface", "rail", "field", "sunken", "accent-soft"]) {
    const back = rgb(theme[surface]);
    const over = (text: string) => rgb(theme[text]).map((v, i) => Math.round(alpha * v + (1 - alpha) * back[i]));
    expect(contrast(over("ink"), back), `ink on ${surface}`).toBeGreaterThanOrEqual(4.5);
    for (const text of ["muted", "faint"]) expect(contrast(over(text), back), `${text} on ${surface}`).toBeGreaterThanOrEqual(3);
  }
});

// Tailwind's preflight sets `button, input, select, textarea { opacity: 1 }` in the same
// base layer, at an element's specificity. A :where() fade (zero specificity) loses to it,
// and disabled controls looked enabled: the fade's selector must outrank one element.
it("fades disabled controls over preflight's opacity: 1, and still lets a utility win", () => {
  const preflight = readFileSync(require.resolve("tailwindcss/preflight.css"), "utf8");
  expect(preflight).toMatch(/opacity:\s*1/);
  const [selector] = [...baseRules()].find(([sel]) => sel.includes(":disabled")) ?? [""];
  expect(selector.startsWith(":is(")).toBe(true);
  // :is() takes its most specific part: an attribute selector, above preflight's element.
  expect(selector).toMatch(/:is\([^)]*\[role="button"\]/);
  // In the base layer, which Tailwind's utilities layer always beats.
  expect(css.indexOf(selector.slice(0, 20))).toBeGreaterThan(css.indexOf("@layer base {"));
});

it("draws one 2px focus ring with an offset", () => {
  const ring = baseRules().get(":focus-visible") ?? "";
  expect(ring).toMatch(/outline:\s*2px solid var\(--color-ink\)/);
  expect(ring).toMatch(/outline-offset:\s*2px/);
});

it("reaches the controls the app draws, and not plain text", () => {
  const pointer = selectorFor("pointer");
  const disabled = selectorFor("not-allowed");
  render(
    <MemoryRouter>
      <p>Plain answer text</p>
      <Button>Export</Button>
      <Button disabled>Send</Button>
      <Tabs tabs={[{ id: "a", label: "Outputs" }, { id: "b", label: "Queries" }]} value="a" onChange={() => undefined} />
      <InfoTip term="rigor-review" />
      <details>
        <summary>SQL the answer quotes</summary>
      </details>
      <label>
        <input type="checkbox" /> Rigor review
      </label>
      <textarea aria-label="Your question" />
    </MemoryRouter>,
  );
  expect(screen.getByRole("button", { name: "Export" }).matches(pointer)).toBe(true);
  expect(screen.getByRole("button", { name: "Send" }).matches(disabled)).toBe(true);
  expect(screen.getByRole("tab", { name: "Queries" }).matches(pointer)).toBe(true);
  expect(screen.getByRole("button", { name: /About/ }).matches(pointer)).toBe(true);
  expect(screen.getByText("SQL the answer quotes").matches(pointer)).toBe(true);
  expect(screen.getByRole("checkbox").closest("label")?.matches(pointer)).toBe(true);
  expect(screen.getByRole("textbox", { name: "Your question" }).matches(selectorFor("text"))).toBe(true);
  expect(screen.getByText("Plain answer text").matches(pointer)).toBe(false);
});

it("keeps hover off a disabled Button", () => {
  render(<Button disabled>Send</Button>);
  const classes = screen.getByRole("button", { name: "Send" }).className.split(/\s+/);
  expect(classes.filter((c) => c.startsWith("hover:"))).toEqual([]);
});
