import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { Brand, usePracticeTab } from "./brand";

function Tab({ practice }: { practice: boolean }) {
  usePracticeTab(practice);
  return <Brand practice={practice} />;
}

beforeEach(() => {
  document.head.innerHTML = `
    <link rel="icon" type="image/png" href="/favicon-32.png" data-brand />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" data-brand />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" data-brand />`;
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
  expect(hrefs()).toEqual(["/favicon-32.png", "/favicon.svg", "/apple-touch-icon.png"]);
  expect(document.title).toBe("DataLab");
});

it("marks practice in the header, the tab's icon and its title", () => {
  const { unmount } = render(<Tab practice />);
  expect(screen.getByTestId("brand")).toHaveTextContent("DataLabpractice");
  expect(hrefs()).toEqual(["/favicon-practice-32.png", "/favicon-practice.svg", "/apple-touch-icon-practice.png"]);
  expect(document.title).toBe("DataLab (practice)");
  unmount();
  expect(hrefs()).toEqual(["/favicon-32.png", "/favicon.svg", "/apple-touch-icon.png"]);
});
