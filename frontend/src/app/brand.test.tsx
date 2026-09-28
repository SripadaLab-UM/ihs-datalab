import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { Brand, practiceIcon, usePracticeTab } from "./brand";
import { BLOCK_M, COLOURS, IHS_MARK, TILE } from "./brandArt";

function Tab({ practice }: { practice: boolean }) {
  usePracticeTab(practice);
  return <Brand practice={practice} />;
}

// The hashed URLs, as index.html names them (brandIcons.ts).
const icon = (path: string) => __BRAND_ICONS__[path];
const REAL = ["/favicon-32.png", "/favicon.svg", "/apple-touch-icon.png"].map(icon);
const PRACTICE = ["/favicon-practice-32.png", "/favicon-practice.svg", "/apple-touch-icon-practice.png"].map(icon);

beforeEach(() => {
  document.head.innerHTML = `
    <link rel="icon" type="image/png" href="${icon("/favicon-32.png")}" data-brand />
    <link rel="icon" type="image/svg+xml" href="${icon("/favicon.svg")}" data-brand />
    <link rel="apple-touch-icon" href="${icon("/apple-touch-icon.png")}" data-brand />`;
  document.title = "DataLab";
});

afterEach(() => {
  document.head.innerHTML = "";
});

const hrefs = () => [...document.querySelectorAll("link[data-brand]")].map((link) => link.getAttribute("href"));

it("shows the name, and leaves the real DataLab's tab as it is", () => {
  render(<Tab practice={false} />);
  expect(screen.getByTestId("brand")).toHaveTextContent(/^DataLab$/);
  // Only the mark on narrow windows, so the tabs fit.
  expect(screen.getByText("DataLab")).toHaveClass("hidden", "lg:inline");
  expect(hrefs()).toEqual(REAL);
  expect(document.title).toBe("DataLab");
});

it("marks practice in the header, the tab's icon and its title", () => {
  const { unmount } = render(<Tab practice />);
  expect(screen.getByTestId("brand")).toHaveTextContent("DataLabpractice");
  // Practice's own icons, by their own hashes, so an old one is never kept either.
  expect(hrefs()).toEqual(PRACTICE);
  for (const href of hrefs()) expect(href).toMatch(/-practice[.-][a-z0-9.]+\?v=[0-9a-f]{8}$/);
  expect(document.title).toBe("DataLab (practice)");
  unmount();
  expect(hrefs()).toEqual(REAL);
});

it("swaps a hashed icon for practice's hashed one, whatever hash the page had", () => {
  expect(practiceIcon("/favicon.svg?v=00000000")).toBe(__BRAND_ICONS__["/favicon-practice.svg"]);
  expect(practiceIcon("/apple-touch-icon.png")).toBe(__BRAND_ICONS__["/apple-touch-icon-practice.png"]);
  // Already practice's: unchanged.
  expect(practiceIcon(icon("/favicon-practice-32.png"))).toBe(icon("/favicon-practice-32.png"));
});

it("draws the Block M and a big spark on its tile, and plain IHS beside it, from branding/build.py", () => {
  const { rerender } = render(<Brand practice={false} />);
  const [m, ihs] = [...screen.getByTestId("brand").querySelectorAll("svg")];
  // The Block M as traced, Maize on Blue, where the "d." mark was (22 px).
  expect(m).toHaveAttribute("width", "22");
  expect(m.querySelector("rect")).toHaveAttribute("fill", COLOURS.blue);
  expect(m.querySelector("path")).toHaveAttribute("d", BLOCK_M.d);
  expect(m.querySelector("path")).toHaveAttribute("fill", COLOURS.maize);
  // The AI spark over its corner, Maize, edged in the tile's Blue.
  const spark = m.querySelectorAll("path")[1];
  expect(spark).toHaveAttribute("d", TILE.spark?.d);
  expect(spark).toHaveAttribute("fill", COLOURS.maize);
  expect(spark).toHaveAttribute("stroke", COLOURS.blue);
  // Bigger than it was, for 22 px: tip to tip about a third of the tile, and still on it.
  const nums = [...(TILE.spark?.d ?? "").matchAll(/-?[\d.]+/g)].map(Number);
  const xs = nums.filter((_, i) => i % 2 === 0);
  const halo = TILE.spark?.halo ?? 0;
  expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(30);
  expect(Math.max(...nums) + halo).toBeLessThanOrEqual(64);
  expect(Math.min(...nums) - halo).toBeGreaterThanOrEqual(0);
  // "IHS", a drawing of its own beside the name on the widest windows (so the tabs
  // fit at 1024): the letters alone, since the tile has the spark.
  expect(ihs).toHaveAttribute("data-mark", "letters");
  expect(ihs.querySelectorAll("path")).toHaveLength(IHS_MARK.paths.length);
  expect(IHS_MARK.paths).toHaveLength(6); // I, H (three bars), S (two arcs)
  expect(ihs.parentElement).toHaveClass("hidden", "xl:inline-flex");
  // Practice: inverted, Blue on Maize.
  rerender(<Brand practice />);
  const practice = screen.getByTestId("brand").querySelector("svg");
  expect(practice?.querySelector("rect")).toHaveAttribute("fill", COLOURS.maize);
  expect(practice?.querySelector("path")).toHaveAttribute("fill", COLOURS.blue);
});
