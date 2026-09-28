import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { THEME_KEY } from "@/lib/theme";

import { AppearanceSection } from "./AppearanceSection";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterEach(() => vi.restoreAllMocks());

it("starts on System, and applies and keeps Dark and Light", () => {
  render(<AppearanceSection />);
  const group = screen.getByRole("group", { name: "Theme" });
  expect(group).toBeTruthy();
  expect((screen.getByRole("radio", { name: "System" }) as HTMLInputElement).checked).toBe(true);

  fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem(THEME_KEY)).toBe("dark");

  fireEvent.click(screen.getByRole("radio", { name: "Light" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem(THEME_KEY)).toBe("light");

  fireEvent.click(screen.getByRole("radio", { name: "System" }));
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(localStorage.getItem(THEME_KEY)).toBeNull();
});

it("opens on the saved choice", () => {
  localStorage.setItem(THEME_KEY, "dark");
  render(<AppearanceSection />);
  expect((screen.getByRole("radio", { name: "Dark" }) as HTMLInputElement).checked).toBe(true);
});

it("still applies the choice when the browser won't keep it, and says so", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("SecurityError");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("SecurityError");
  });
  render(<AppearanceSection />);
  expect((screen.getByRole("radio", { name: "System" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(screen.getByRole("status").textContent).toMatch(/won't let DataLab remember/);
});
