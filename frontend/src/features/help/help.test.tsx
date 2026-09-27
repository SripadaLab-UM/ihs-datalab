import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { Shell } from "@/app/Shell";

import { HelpPage } from "./HelpPage";
import { resetTourMemory } from "./Tour";

vi.mock("@/api/client", () => ({
  SIGNED_OUT: "datalab:signed-out",
  api: { health: vi.fn() },
}));

function app(at: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<Shell />}>
            <Route
              path="workspace"
              element={
                <footer data-tour="composer">
                  <textarea aria-label="Your question" />
                </footer>
              }
            />
            <Route path="sql" element={<p>The SQL editor</p>} />
            <Route path="help/:slug?" element={<HelpPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  resetTourMemory();
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
});
afterEach(() => vi.restoreAllMocks());

// --- Help ---------------------------------------------------------------

it("opens on the current screen's topic, and goes back where it came from", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  app("/sql");
  fireEvent.click(screen.getByRole("link", { name: "Help: The SQL Playground" }));
  expect(await screen.findByRole("heading", { level: 1, name: "The SQL Playground" })).toHaveFocus();
  expect(screen.getByRole("link", { name: "The SQL Playground", current: "page" })).toBeInTheDocument();

  // Moving between Help's pages keeps the way back.
  fireEvent.click(screen.getByRole("link", { name: "Glossary" }));
  await screen.findByRole("heading", { level: 1, name: "Glossary" });
  fireEvent.click(screen.getByRole("link", { name: /Back to the SQL Playground/ }));
  expect(await screen.findByText("The SQL editor")).toBeInTheDocument();
});

it("goes to a glossary term by its anchor", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  app("/help/glossary#rigor-review");
  const heading = await screen.findByRole("heading", { name: "Rigor review" });
  expect(heading).toHaveAttribute("id", "rigor-review");
  expect(heading).toHaveFocus();
});

it("searches the guide and the glossary, and opens a result", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  app("/help");
  await screen.findByRole("heading", { level: 1, name: "Start here" });
  const search = screen.getByRole("searchbox", { name: "Search Help" });
  fireEvent.change(search, { target: { value: "replay" } });
  const results = screen.getByRole("region", { name: "Search results" });
  expect(within(results).getByRole("status")).toHaveTextContent(/\d+ results?/);
  fireEvent.click(within(results).getByRole("link", { name: /^Run a workflow/ }));
  expect(await screen.findByRole("heading", { level: 1, name: "Run a workflow" })).toBeInTheDocument();

  fireEvent.change(search, { target: { value: "qqqzzz" } });
  expect(screen.getByRole("status")).toHaveTextContent("Nothing found");
  fireEvent.keyDown(search, { key: "Escape" });
  expect(screen.getByRole("navigation", { name: "Help" })).toHaveTextContent("Working with the agent");
});

it("follows links between guide pages inside Help", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  app("/help/first-question");
  await screen.findByRole("heading", { level: 1, name: "Ask your first question" });
  const link = screen.getAllByRole("link", { name: "analysis plan" })[0];
  expect(link).toHaveAttribute("href", "/help/plans");
  fireEvent.click(link);
  expect(await screen.findByRole("heading", { level: 1, name: "Analysis plans and pilots" })).toBeInTheDocument();
});

// --- The tour -----------------------------------------------------------

it("starts once on the practice DataLab, can be skipped, and isn't shown again", async () => {
  const first = app("/workspace");
  const tour = await screen.findByRole("dialog", { name: "Ask a question" });
  expect(tour).toHaveTextContent("1 of 5");
  // It points at the step's place on screen.
  expect(document.querySelector("[data-tour=composer]")).toHaveAttribute("data-tour-on");

  fireEvent.click(within(tour).getByRole("button", { name: "Next" }));
  expect(screen.getByRole("dialog", { name: "Watch the steps" })).toHaveTextContent("2 of 5");
  fireEvent.click(screen.getByRole("button", { name: "Skip the tour" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.querySelector("[data-tour-on]")).toBeNull();
  expect(localStorage.getItem("datalab.tour.seen")).toBe("1");

  first.unmount();
  resetTourMemory();
  app("/workspace");
  await waitFor(() => expect(api.health).toHaveBeenCalledTimes(2));
  await screen.findByRole("textbox", { name: "Your question" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("closes with Escape", async () => {
  app("/workspace");
  const tour = await screen.findByRole("dialog", { name: "Ask a question" });
  fireEvent.keyDown(tour, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("doesn't start by itself on the real DataLab", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  app("/workspace");
  await waitFor(() => expect(api.health).toHaveBeenCalled());
  await screen.findByRole("textbox", { name: "Your question" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("is replayed from Help, all five steps", async () => {
  localStorage.setItem("datalab.tour.seen", "1");
  app("/help/tour");
  fireEvent.click(await screen.findByRole("button", { name: "Take the tour" }));
  const tour = await screen.findByRole("dialog", { name: "Ask a question" });
  for (let step = 1; step < 5; step++) fireEvent.click(within(tour).getByRole("button", { name: "Next" }));
  expect(tour).toHaveAccessibleName("Find the outputs and export");
  expect(within(tour).queryByRole("button", { name: "Skip the tour" })).toBeNull();
  fireEvent.click(within(tour).getByRole("button", { name: "Back" }));
  expect(tour).toHaveAccessibleName("Read the answer, its trace and review");
  fireEvent.click(within(tour).getByRole("button", { name: "Next" }));
  fireEvent.click(within(tour).getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("still works when the browser won't store that it was seen", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  app("/workspace");
  fireEvent.click(await screen.findByRole("button", { name: "Skip the tour" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  // Not again on this page, although it couldn't be stored.
  fireEvent.click(screen.getByRole("link", { name: /^Help/ }));
  fireEvent.click(await screen.findByRole("link", { name: /Back to the Workspace/ }));
  await screen.findByRole("textbox", { name: "Your question" });
  expect(screen.queryByRole("dialog")).toBeNull();
});
