import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { Shell } from "@/app/Shell";

vi.mock("@/api/client", () => ({
  SIGNED_OUT: "datalab:signed-out",
  api: { health: vi.fn() },
}));
vi.mock("@/api/github", () => ({ githubApi: { status: vi.fn().mockResolvedValue({ available: false }) } }));

function app(at: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="*" element={<p>page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const scrolled: Element[] = [];
beforeEach(() => {
  scrolled.length = 0;
  // jsdom has no layout; record which element asked to be shown.
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    scrolled.push(this);
  });
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
});
afterEach(() => {
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

it("keeps the tab you're on in view when the tabs scroll", async () => {
  app("/settings");
  const tab = screen.getByRole("link", { current: "page" });
  expect(tab).toHaveAttribute("href", "/settings");
  await waitFor(() => expect(scrolled).toContain(tab));
});

it("has a short Settings label for narrow windows, and the full one for wide", () => {
  app("/workspace");
  const tab = screen.getByRole("link", { name: /Settings/ });
  const [short, full] = [...tab.querySelectorAll("span")];
  expect(short).toHaveTextContent(/^Settings$/);
  expect(short).toHaveClass("xl:hidden");
  expect(full).toHaveTextContent("Settings & Safety");
  expect(full).toHaveClass("hidden", "xl:inline");
});

it("says practice in the badge at every width, and synthetic data when there's room", async () => {
  app("/workspace");
  const badge = await screen.findByTitle(/The practice profile/);
  expect(badge).toHaveTextContent("practice · synthetic data");
  expect(badge.querySelector("span")).toHaveClass("hidden", "lg:inline");
});
