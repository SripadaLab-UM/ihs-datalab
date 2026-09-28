import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyTheme, isDark, readTheme, saveTheme, THEME_KEY } from "./theme";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterEach(() => vi.restoreAllMocks());

describe("the theme", () => {
  it("defaults to System", () => {
    expect(readTheme()).toBe("system");
    localStorage.setItem(THEME_KEY, "purple");
    expect(readTheme()).toBe("system");
  });

  it("keeps Light and Dark, and forgets the choice for System", () => {
    expect(saveTheme("dark")).toBe(true);
    expect(readTheme()).toBe("dark");
    saveTheme("light");
    expect(readTheme()).toBe("light");
    saveTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(readTheme()).toBe("system");
  });

  it("follows the computer when the browser refuses storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(readTheme()).toBe("system");
    expect(saveTheme("dark")).toBe(false);
  });

  it("sets data-theme on <html>, and none for System", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(isDark()).toBe(true);
    applyTheme("light");
    expect(isDark()).toBe(false);
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  describe("public/theme-boot.js", () => {
    const FRONTEND = resolve(__dirname, "../..");
    const boot = () => new Function(readFileSync(resolve(FRONTEND, "public/theme-boot.js"), "utf8"))();

    it("is an ordinary script in <head>, before the app's module and its stylesheet", () => {
      const html = readFileSync(resolve(FRONTEND, "index.html"), "utf8");
      const head = html.slice(0, html.indexOf("</head>"));
      const tag = /<script src="\/theme-boot\.js"><\/script>/.exec(head);
      expect(tag).not.toBeNull();
      expect(tag![0]).not.toContain("module");
      expect(html.indexOf("/theme-boot.js")).toBeLessThan(html.indexOf("/src/main.tsx"));
      expect(html.indexOf("/theme-boot.js")).toBeLessThan(Math.max(html.indexOf("stylesheet"), html.length));
    });

    it("sets the same data-theme as the app, from the same key", () => {
      localStorage.setItem(THEME_KEY, "dark");
      boot();
      expect(document.documentElement.dataset.theme).toBe("dark");
      delete document.documentElement.dataset.theme;
      localStorage.setItem(THEME_KEY, "purple");
      boot();
      expect(document.documentElement.dataset.theme).toBeUndefined();
    });

    it("leaves System alone when the browser refuses storage", () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("SecurityError");
      });
      expect(boot).not.toThrow();
      expect(document.documentElement.dataset.theme).toBeUndefined();
    });
  });

  it("has the same dark colours whether chosen or the computer's", () => {
    const css = readFileSync(resolve(__dirname, "../styles/index.css"), "utf8");
    const block = (selector: string) => {
      const start = css.indexOf(`${selector} {`);
      expect(start, selector).toBeGreaterThan(-1);
      const body = css.slice(start, css.indexOf("}", start));
      return [...body.matchAll(/(--color-[\w-]+):\s*([^;]+);/g)].map((m) => `${m[1]}:${m[2]}`);
    };
    const chosen = block(':root[data-theme="dark"]');
    const computer = block(':root:not([data-theme="light"])');
    expect(chosen.length).toBeGreaterThan(15);
    expect(chosen).toEqual(computer);
  });
});
