import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  document.querySelector("base")?.remove();
  vi.resetModules();
});

async function load() {
  return import("./base");
}

it("is the site's root without a <base> element, whatever the page's address", async () => {
  window.history.pushState({}, "", "/workspace/c_1");
  const { BASE, at } = await load();
  expect(BASE).toBe("");
  expect(at("/api/health")).toBe("/api/health");
});

it("is the path the server names in <base href>", async () => {
  const base = document.createElement("base");
  base.setAttribute("href", "/datalab/");
  document.head.append(base);
  const { BASE, at } = await load();
  expect(BASE).toBe("/datalab");
  expect(at("/api/conversations")).toBe("/datalab/api/conversations");
  expect(at("relative")).toBe("relative");
});
