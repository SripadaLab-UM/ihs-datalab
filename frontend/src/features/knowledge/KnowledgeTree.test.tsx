import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";

import { KnowledgePage } from "./KnowledgePage";
import { OPEN_KEY } from "./KnowledgeTree";
import { FIXTURE } from "./tree.fixture";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: {
    status: vi.fn(),
    sync: vi.fn(),
    pages: vi.fn(),
    page: vi.fn(),
    history: vi.fn(),
  },
}));
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: () => <div>docked chat</div>,
}));
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: ({ value }: { value: string }) => <pre>{value}</pre>,
}));

const STATUS: KnowledgeStatus = {
  available: true, repo: "in sync", name: "SripadaLab-UM/ihs-knowledge", signed_in: true,
  account: { login: "yfang", name: "Yu Fang" }, head: "abc1234def", last_sync: new Date().toISOString(),
  last_error: null, ahead: 0, behind: 0, message: null,
}; // prettier-ignore

function show(at = "/knowledge") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="knowledge/*" element={<KnowledgePage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const tree = () => screen.getByRole("tree", { name: "Pages and skills" });
const item = (name: string | RegExp) => within(tree()).getByRole("treeitem", { name });
const shownItems = () =>
  within(tree())
    .queryAllByRole("treeitem")
    .map((el) => el.getAttribute("aria-label"));
const expanded = (name: string | RegExp) => item(name).getAttribute("aria-expanded");
const chevron = (name: string | RegExp) => item(name).querySelector<HTMLElement>(":scope > div > span[aria-hidden]")!;
const rowOf = (name: string | RegExp) => item(name).querySelector<HTMLElement>(":scope > div")!;
const SECTIONS = [
  "Study documentation, 4 pages",
  "Data sources, 8 pages",
  "Analysis methods, 1 page",
  "Quality checks, 1 page",
  "Workflows and pipelines, 2 pages",
];

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  window.matchMedia = vi.fn(() => ({ matches: true })) as never;
  vi.mocked(knowledgeApi.status).mockReset().mockResolvedValue(STATUS);
  vi.mocked(knowledgeApi.pages).mockReset().mockResolvedValue({ head: "abc1234def", pages: FIXTURE });
  vi.mocked(knowledgeApi.page).mockReset().mockImplementation(async (path) => ({
    path, place: "page", head: "abc1234def", text: "…", front_matter: null,
    body: `# Page ${path}\n\nSee [the ring table](/tables/IHS_2031.RINGSLEEP.md).\n`,
  })); // prettier-ignore
  vi.mocked(knowledgeApi.history).mockReset().mockResolvedValue([]);
});
afterEach(() => vi.restoreAllMocks());

it("starts with every section folded, even with the index open", async () => {
  show();
  expect(await screen.findByRole("heading", { name: "Page index.md" })).toBeTruthy();
  expect(shownItems()).toEqual(SECTIONS);
  for (const name of SECTIONS) expect(expanded(name)).toBe("false");
  expect(item("Data sources, 8 pages").getAttribute("aria-level")).toBe("1");
  // Counts beside the headings, and a pointer on them.
  expect(rowOf("Data sources, 8 pages").textContent).toContain("· 8");
  expect(rowOf("Data sources, 8 pages").className).toContain("cursor-pointer");
});

it("opens what holds a page from a deep link, and selects it", async () => {
  show("/knowledge/tables/IHS_2030.BANDDAILY.md");
  expect(
    await screen.findByRole("heading", {
      name: "Page tables/IHS_2030.BANDDAILY.md",
    }),
  ).toBeTruthy();
  const row = await screen.findByRole("treeitem", {
    name: "IHS_2030.BANDDAILY",
  });
  expect(row.getAttribute("aria-selected")).toBe("true");
  expect(row.getAttribute("aria-level")).toBe("4");
  expect(expanded("Data sources, 8 pages")).toBe("true");
  expect(expanded("wristband")).toBe("true");
  expect(expanded("IHS_2030, 1 page")).toBe("true");
  // The rest stays folded.
  expect(expanded("IHS_2031, 1 page")).toBe("false");
  expect(expanded("ring")).toBe("false");
  expect(expanded("Quality checks, 1 page")).toBe("false");
});

it("opens what holds a page followed from a link in another page", async () => {
  show("/knowledge/qc/nap_window.md");
  expect(await screen.findByRole("heading", { name: "Page qc/nap_window.md" })).toBeTruthy();
  expect(expanded("Data sources, 8 pages")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "the ring table" }));
  expect(
    await screen.findByRole("heading", {
      name: "Page tables/IHS_2031.RINGSLEEP.md",
    }),
  ).toBeTruthy();
  expect(expanded("Data sources, 8 pages")).toBe("true");
  expect(item("IHS_2031.RINGSLEEP").getAttribute("aria-selected")).toBe("true");
  expect(item("nap_window").getAttribute("aria-selected")).toBe("false");
  // A summary that names the page first says only the rest.
  expect(rowOf("IHS_2031.RINGSLEEP").textContent).toBe("IHS_2031.RINGSLEEPRing sleep table.");
});

it("keeps the person's own choices while moving between pages, and across reloads", async () => {
  const { unmount } = show("/knowledge/tables/IHS_2031.RINGSLEEP.md");
  await screen.findByRole("treeitem", { name: "IHS_2031.RINGSLEEP" });
  fireEvent.click(chevron("Quality checks, 1 page"));
  fireEvent.click(rowOf("Workflows and pipelines, 2 pages")); // a heading's row folds and unfolds too
  expect(expanded("Workflows and pipelines, 2 pages")).toBe("true");
  // Folding what holds the open page sticks.
  fireEvent.click(chevron("ring"));
  expect(expanded("ring")).toBe("false");
  fireEvent.click(rowOf("nap_window"));
  expect(await screen.findByRole("heading", { name: "Page qc/nap_window.md" })).toBeTruthy();
  expect(item("nap_window").getAttribute("aria-selected")).toBe("true");
  expect(expanded("ring")).toBe("false");
  expect(expanded("Data sources, 8 pages")).toBe("true");
  expect(expanded("Workflows and pipelines, 2 pages")).toBe("true");
  unmount();

  show("/knowledge");
  await screen.findByRole("heading", { name: "Page index.md" });
  await screen.findByRole("treeitem", { name: "nap_window" });
  expect(expanded("Quality checks, 1 page")).toBe("true");
  expect(expanded("Workflows and pipelines, 2 pages")).toBe("true");
  expect(expanded("ring")).toBe("false");
  expect(JSON.parse(localStorage.getItem(OPEN_KEY)!)).toContain("section:qc");
});

it("still works when storage is refused, for as long as the page lasts", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("refused", "SecurityError");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("refused", "SecurityError");
  });
  show("/knowledge/qc/nap_window.md");
  await screen.findByRole("treeitem", { name: "nap_window" });
  fireEvent.click(chevron("Analysis methods, 1 page"));
  fireEvent.click(chevron("Derived features, 1 page"));
  fireEvent.click(rowOf("nap_minutes, draft"));
  expect(
    await screen.findByRole("heading", {
      name: "Page features/nap_minutes.md",
    }),
  ).toBeTruthy();
  expect(expanded("Quality checks, 1 page")).toBe("true");
  expect(item("nap_minutes, draft").getAttribute("aria-selected")).toBe("true");
});

it("searches inside folded sections, shows the matches opened, and goes back to the person's choices when cleared", async () => {
  show("/knowledge/qc/nap_window.md");
  await screen.findByRole("treeitem", { name: "nap_window" });
  fireEvent.click(chevron("Quality checks, 1 page")); // the person folds it
  const search = screen.getByLabelText("Search pages and skills");
  fireEvent.change(search, { target: { value: "2031 sleep" } });
  expect(screen.getByText("1 matching page")).toBeTruthy();
  expect(shownItems()).toEqual(["Data sources, 1 page", "ring", "IHS_2031.RINGSLEEP"]);
  expect(expanded("ring")).toBe("true");
  // A search the person folds part of, then another search: each starts opened.
  fireEvent.change(search, { target: { value: "nap" } });
  expect(shownItems()).toContain("nap_window");
  expect(item("nap_window").getAttribute("aria-selected")).toBe("true"); // the page open is highlighted
  fireEvent.click(chevron("Analysis methods, 1 page"));
  expect(expanded("Analysis methods, 1 page")).toBe("false");
  // Opening a result keeps it in view once the search is cleared.
  fireEvent.click(rowOf("check.R"));
  expect(await screen.findByText("…")).toBeTruthy(); // a script, shown as code
  expect(item("check.R").getAttribute("aria-selected")).toBe("true");
  fireEvent.change(search, { target: { value: "" } });
  expect(expanded("Quality checks, 1 page")).toBe("false");
  expect(expanded("Data sources, 8 pages")).toBe("false");
  expect(expanded("Analysis methods, 1 page")).toBe("false");
  expect(expanded("nap-check")).toBe("true");
  expect(item("check.R").getAttribute("aria-selected")).toBe("true");
});

it("moves with the keyboard as the WAI-ARIA tree does", async () => {
  show("/knowledge/qc/nap_window.md");
  await screen.findByRole("treeitem", { name: "nap_window" });
  // One row in the tab order: the page open.
  expect(
    within(tree())
      .getAllByRole("treeitem")
      .filter((el) => el.tabIndex === 0)
      .map((el) => el.getAttribute("aria-label")),
  ).toEqual(["nap_window"]);
  const key = (name: string) => act(() => void fireEvent.keyDown(document.activeElement!, { key: name }));
  const focused = () => document.activeElement?.getAttribute("aria-label");

  act(() => item("Study documentation, 4 pages").focus());
  key("ArrowDown");
  expect(focused()).toBe("Data sources, 8 pages");
  key("ArrowRight"); // opens
  expect(expanded("Data sources, 8 pages")).toBe("true");
  expect(focused()).toBe("Data sources, 8 pages");
  key("ArrowRight"); // into it
  expect(focused()).toBe("diary");
  key("ArrowDown");
  key("ArrowRight"); // a source with tables opens too
  expect(expanded("ring")).toBe("true");
  key("ArrowDown");
  expect(focused()).toBe("IHS_2031.RINGSLEEP");
  key("ArrowLeft"); // to the parent
  expect(focused()).toBe("ring");
  key("ArrowLeft"); // folds it
  expect(expanded("ring")).toBe("false");
  key("ArrowLeft");
  expect(focused()).toBe("Data sources, 8 pages");
  key("End");
  expect(focused()).toBe("Workflows and pipelines, 2 pages");
  key("Home");
  expect(focused()).toBe("Study documentation, 4 pages");
  key("Enter"); // a heading folds and unfolds
  expect(expanded("Study documentation, 4 pages")).toBe("true");
  key("ArrowDown");
  key("ArrowDown");
  expect(focused()).toBe("README.md");
  key("Enter"); // a page opens
  expect(await screen.findByRole("heading", { name: "Page README.md" })).toBeTruthy();
  expect(item("README.md").getAttribute("aria-selected")).toBe("true");
  // From the search box, down goes into the tree.
  const search = screen.getByLabelText("Search pages and skills");
  fireEvent.change(search, { target: { value: "drift" } });
  act(() => search.focus());
  key("ArrowDown");
  expect(focused()).toBe("drift.md");
});
