import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { Shell } from "@/app/Shell";

vi.mock("@/api/client", () => ({
  SIGNED_OUT: "datalab:signed-out",
  api: { health: vi.fn(), destinations: vi.fn().mockResolvedValue([]) },
}));
vi.mock("@/api/settings", () => ({
  settingsApi: {
    connections: vi.fn().mockReturnValue(new Promise(() => {})),
    updateCheck: vi.fn().mockReturnValue(new Promise(() => {})),
  },
}));
vi.mock("@/api/docker", () => ({ dockerApi: { status: vi.fn().mockResolvedValue({ state: "unsupported", fixing: false, admin_access_url: null }) } }));
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
  expect(short).toHaveClass("2xl:hidden");
  expect(full).toHaveTextContent("Settings & Safety");
  expect(full).toHaveClass("hidden", "2xl:inline");
});

it("says practice at every width (the badge, or the brand from lg to 2xl), and synthetic data when there's room", async () => {
  app("/workspace");
  const badge = await screen.findByTitle(/The practice profile/);
  expect(badge).toHaveTextContent("practice · synthetic data");
  expect(badge.querySelector("span")).toHaveClass("hidden", "xl:inline");
  // Where the badge steps aside (lg to 2xl), the brand says practice.
  expect(within(screen.getByTestId("brand")).getByText("practice")).toHaveClass("lg:inline");
});

it("has the shortcuts at the right: Database and the key always, GitHub and folders from 2xl up", async () => {
  app("/workspace");
  const header = screen.getByRole("banner");
  const shortcuts = within(header).getByRole("group", { name: "Connections and folders" });
  expect(within(shortcuts).getByRole("button", { name: /^Database/ })).toBeVisible();
  expect(within(shortcuts).getByRole("button", { name: /^U-M GPT key/ })).toBeVisible();
  // Below xl, More holds them instead, so the tabs never overflow.
  const folders = within(shortcuts).getByRole("button", { name: /^Export folders/ });
  expect(folders.closest(".\\32xl\\:flex")).toHaveClass("hidden", "2xl:flex");
  expect(within(header).getByRole("button", { name: "More" })).toHaveAttribute("aria-haspopup", "menu");
});

it("keeps End session in its own menu at the far end, set apart from More", () => {
  app("/workspace");
  const header = screen.getByRole("banner");
  const session = within(header).getByRole("button", { name: "Session" });
  const more = within(header).getByRole("button", { name: "More" });
  expect(session).not.toBe(more);
  const right = session.closest(".ml-auto")!;
  const buttons = [...right.querySelectorAll("button[aria-haspopup]")];
  expect(buttons.at(-1)).toBe(session);
  expect(session.closest(".border-l")).not.toBeNull();
});

it("makes room for the shortcuts on narrower windows: tighter gaps, the badge only where the brand doesn't say practice", async () => {
  app("/workspace");
  const badge = await screen.findByTitle(/The practice profile/);
  expect(badge).toHaveClass("lg:hidden", "2xl:inline");
  expect(badge.closest(".ml-auto")).toHaveClass("gap-2", "2xl:gap-3");
});
