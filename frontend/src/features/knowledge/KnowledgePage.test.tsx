import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { type KbEntry, type KbPage, knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";
import type { ChatContext } from "@/components/chat/DockedChat";

import { KnowledgePage } from "./KnowledgePage";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: { status: vi.fn(), sync: vi.fn(), pages: vi.fn(), page: vi.fn(), history: vi.fn() },
}));
const chat = vi.hoisted(() => ({ props: null as null | { mode: string; context?: ChatContext } }));
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: (props: { mode: string; context?: ChatContext; headerActions?: ReactNode }) => {
    chat.props = props;
    return <div>docked chat{props.headerActions}</div>;
  },
}));
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: ({ label, value }: { label: string; value: string }) => <pre aria-label={label}>{value}</pre>,
}));

const status = (more: Partial<KnowledgeStatus> = {}): KnowledgeStatus => ({
  available: true, repo: "in sync", name: "SripadaLab-UM/ihs-knowledge", signed_in: true,
  account: { login: "yfang", name: "Yu Fang" }, head: "abc1234def", last_sync: new Date().toISOString(),
  last_error: null, ahead: 0, behind: 0, message: null, ...more,
}); // prettier-ignore
const entry = (path: string, place: string, title: string, more: Partial<KbEntry> = {}): KbEntry => ({
  path, place, title, summary: "", size: 10, status: null, kind: null, related: [], cohorts: [], ...more,
}); // prettier-ignore
const ENTRIES = [
  entry("AGENTS.md", "top", "AGENTS.md"),
  entry("index.md", "top", "index.md"),
  entry("sources/fitbit.md", "page", "fitbit", { status: "reviewed", summary: "Fitbit trackers." }),
  entry("qc/midnight-sleep.md", "page", "midnight-sleep", { status: "draft" }),
  entry("skills/steps-check/SKILL.md", "skill", "steps-check", { summary: "Check steps." }),
  entry("skills/steps-check/check.R", "skill_file", "check.R"),
];
const PAGES: Record<string, KbPage> = {
  "index.md": { path: "index.md", place: "top", head: "abc1234def", text: "# Index\n", front_matter: null, body: "# Knowledge base index\n\n- [fitbit](sources/fitbit.md)\n" },
  "sources/fitbit.md": {
    path: "sources/fitbit.md", place: "page", head: "abc1234def", text: "---\nid: fitbit\n---\n# Fitbit\n",
    front_matter: {
      id: "fitbit", kind: "source", status: "reviewed", summary: "Fitbit trackers, daily summaries from 2021 on.",
      evidence: [{ schema: "IHS_2025.VFITBITDAILYDATA.TRACKERSTEPS" }], limitations: ["Wear time isn't recorded."],
      related: ["qc/midnight-sleep"], cohorts: [2025], reviewed_by: "yfang", reviewed_on: "2026-09-01",
    },
    body: "\n# Fitbit\n\nSee [the sleep rule](../qc/midnight-sleep.md).\n",
  },
  "qc/midnight-sleep.md": {
    path: "qc/midnight-sleep.md", place: "page", head: "abc1234def", text: "…", front_matter: { id: "midnight-sleep", status: "draft" },
    body: "# Midnight-spanning sleep\n",
  },
  "skills/steps-check/check.R": { path: "skills/steps-check/check.R", place: "skill_file", head: "abc1234def", text: "keep <- wear >= 10\n", front_matter: null, body: "keep <- wear >= 10\n" },
}; // prettier-ignore

function show(at = "/knowledge") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="knowledge/*" element={<KnowledgePage />} />
          <Route path="settings/*" element={<p>the settings page</p>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  chat.props = null;
  sessionStorage.clear();
  localStorage.clear();
  window.matchMedia = vi.fn(() => ({ matches: true })) as never; // wide: the chat starts open
  vi.mocked(knowledgeApi.status).mockReset().mockResolvedValue(status());
  vi.mocked(knowledgeApi.pages).mockReset().mockResolvedValue({ head: "abc1234def", pages: ENTRIES });
  vi.mocked(knowledgeApi.page).mockReset().mockImplementation(async (path) => PAGES[path]);
  vi.mocked(knowledgeApi.history).mockReset().mockResolvedValue([]);
  vi.mocked(knowledgeApi.sync).mockReset().mockResolvedValue(status());
});

it("says why there's nothing, when the knowledge base isn't set up (practice)", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue(
    status({ available: false, repo: "not configured", message: "Practice DataLab doesn't use the lab's repositories." }),
  );
  show();
  expect(await screen.findByText("Practice DataLab doesn't use the lab's repositories.")).toBeTruthy();
  expect(knowledgeApi.pages).not.toHaveBeenCalled();
  expect(screen.queryByText("docked chat")).toBeNull();
});

it("lists pages and skills by section, opening on the index", async () => {
  show();
  expect(await screen.findByRole("heading", { name: "Knowledge base index" })).toBeTruthy();
  const tree = screen.getByRole("tree", { name: "Pages and skills" });
  expect(within(tree).getAllByRole("treeitem").map((el) => el.getAttribute("aria-label"))).toEqual([
    "Study documentation, 2 pages", "Data sources, 1 page", "Quality checks, 1 page", "Workflows and pipelines, 2 pages",
  ]); // prettier-ignore
  fireEvent.click(screen.getByRole("treeitem", { name: "Study documentation, 2 pages" }).firstElementChild!);
  expect(screen.getByRole("treeitem", { name: "index.md" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("treeitem", { name: "Quality checks, 1 page" }).firstElementChild!);
  // Drafts say so in the list; reviewed pages don't need to.
  expect(screen.getByRole("treeitem", { name: "midnight-sleep, draft" }).textContent).toContain("draft");
  fireEvent.change(screen.getByLabelText("Search pages and skills"), { target: { value: "trackers" } });
  expect(screen.queryByRole("treeitem", { name: /midnight-sleep/ })).toBeNull();
  expect(screen.getByRole("treeitem", { name: "fitbit" }).textContent).toContain("Fitbit trackers.");
});

it("shows a page's front matter as facts and its text as Markdown, and follows links between pages", async () => {
  show("/knowledge/sources/fitbit.md");
  expect(await screen.findByRole("heading", { name: "Fitbit" })).toBeTruthy();
  expect(screen.getByText("Fitbit trackers, daily summaries from 2021 on.")).toBeTruthy();
  expect(screen.getByText("reviewed")).toBeTruthy();
  expect(screen.getByText("Reviewed by @yfang on 2026-09-01")).toBeTruthy();
  expect(screen.getByText("IHS_2025.VFITBITDAILYDATA.TRACKERSTEPS")).toBeTruthy();
  expect(screen.getByText("Wear time isn't recorded.")).toBeTruthy();
  expect(screen.getByRole("link", { name: /On GitHub/ }).getAttribute("href")).toBe(
    "https://github.com/SripadaLab-UM/ihs-knowledge/blob/abc1234def/sources/fitbit.md",
  );
  // The docked chat can take the page along.
  expect(chat.props?.mode).toBe("knowledge");
  await waitFor(() => expect(chat.props?.context?.label).toBe("The page open in the Knowledge tab (sources/fitbit.md)"));
  // A link in the text opens the page it points to, in the tab.
  fireEvent.click(screen.getByRole("button", { name: "the sleep rule" }));
  expect(await screen.findByRole("heading", { name: "Midnight-spanning sleep" })).toBeTruthy();
  expect(screen.getByText("Draft: no one has reviewed this page yet.")).toBeTruthy();
});

it("shows a skill's script as code", async () => {
  show("/knowledge/skills/steps-check/check.R");
  expect((await screen.findByLabelText("skills/steps-check/check.R")).textContent).toBe("keep <- wear >= 10\n");
});

it("says so for a page that isn't there", async () => {
  show("/knowledge/qc/nothing.md");
  expect(await screen.findByText("There's no qc/nothing.md in the knowledge base as last synced.")).toBeTruthy();
});

it("shows the repo's state and syncs on request", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue(status({ repo: "behind", behind: 2 }));
  show();
  expect(await screen.findByText("2 behind")).toBeTruthy();
  expect(screen.getByText(/GitHub has 2 newer commits/)).toBeTruthy();
  expect(screen.getByText("abc1234")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Sync/ }));
  expect(await screen.findByText("up to date")).toBeTruthy();
  expect(knowledgeApi.sync).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(knowledgeApi.pages).toHaveBeenCalledTimes(2));
});

it("before signing in, sends the person to Settings", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue(status({ repo: "signed out", signed_in: false, message: "Sign in to GitHub to use it." }));
  vi.mocked(knowledgeApi.pages).mockResolvedValue({ head: null, pages: [] });
  show();
  expect(await screen.findByText("Nothing here yet")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Sync/ })).toBeNull();
  fireEvent.click(screen.getAllByRole("link", { name: /Sign in|Settings → GitHub/ })[0]);
  expect(await screen.findByText("the settings page")).toBeTruthy();
});

it("shows recent changes, with links to GitHub and to the pages", async () => {
  vi.mocked(knowledgeApi.history).mockResolvedValue([
    { commit: "bc2ac1b0aa", author: "Yu Fang", date: new Date().toISOString(), subject: "Knowledge: qc/midnight-sleep", paths: ["qc/midnight-sleep.md", "index.md"], changed: 3 },
  ]);
  show();
  fireEvent.click(await screen.findByRole("tab", { name: "Recent changes" }));
  expect(await screen.findByText("Knowledge: qc/midnight-sleep")).toBeTruthy();
  expect(screen.getByRole("link", { name: "bc2ac1b" }).getAttribute("href")).toBe(
    "https://github.com/SripadaLab-UM/ihs-knowledge/commit/bc2ac1b0aa",
  );
  expect(screen.getByText("and 1 more")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "qc/midnight-sleep.md" }));
  expect(await screen.findByRole("heading", { name: "Midnight-spanning sleep" })).toBeTruthy();
});

it("opens a page from an address without .md", async () => {
  show("/knowledge/qc/midnight-sleep");
  expect(await screen.findByRole("heading", { name: "Midnight-spanning sleep" })).toBeTruthy();
  expect(knowledgeApi.page).toHaveBeenCalledWith("qc/midnight-sleep.md");
  expect(screen.getByRole("treeitem", { name: "midnight-sleep, draft" }).getAttribute("aria-selected")).toBe("true");
});

it("never runs what a page's text or front matter holds", async () => {
  const original = PAGES["qc/midnight-sleep.md"];
  const evil = "<img src=x onerror=\"window.__pwned=1\"><script>window.__pwned=2</script>";
  PAGES["qc/midnight-sleep.md"] = {
    path: "qc/midnight-sleep.md", place: "page", head: "abc1234def", text: "…",
    front_matter: { id: evil, status: "draft", summary: evil, limitations: [evil], related: ["javascript:alert(1)"] },
    body: `# Tricks\n\n${evil}\n\n[click me](javascript:window.__pwned=3) and [data](data:text/html,<script>alert(1)</script>)\n\n<a href="javascript:alert(1)">raw</a>\n`,
  }; // prettier-ignore
  const { container } = show("/knowledge/qc/midnight-sleep.md");
  expect(await screen.findByRole("heading", { name: "Tricks" })).toBeTruthy();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img[onerror]")).toBeNull();
  expect([...container.querySelectorAll("a")].filter((a) => /^(javascript|data):/i.test(a.getAttribute("href") ?? ""))).toEqual([]);
  expect([...container.querySelectorAll("*")].filter((el) => [...el.attributes].some((a) => a.name.startsWith("on")))).toEqual([]);
  // Front matter is text.
  expect(screen.getAllByText(evil, { exact: false }).length).toBeGreaterThan(0);
  // A javascript: link is never followed as a page, nor opened without asking.
  fireEvent.click(screen.getByText("click me"));
  expect((window as { __pwned?: number }).__pwned).toBeUndefined();
  PAGES["qc/midnight-sleep.md"] = original;
});

it("over a narrow page, the chat and the list are dialogs: focus in, kept there, Escape closes", async () => {
  window.matchMedia = vi.fn(() => ({ matches: false })) as never;
  show("/knowledge/sources/fitbit.md");
  const ask = await screen.findByRole("button", { name: /Ask for help/ });
  ask.focus();
  fireEvent.click(ask);
  const chatBox = screen.getByRole("dialog", { name: "Knowledge chat" });
  expect(chatBox.getAttribute("aria-modal")).toBe("true");
  await waitFor(() => expect(chatBox.contains(document.activeElement)).toBe(true));
  // Tab from the last control comes back to the first.
  const controls = [...chatBox.querySelectorAll<HTMLElement>("button")];
  controls.at(-1)!.focus();
  fireEvent.keyDown(controls.at(-1)!, { key: "Tab" });
  expect(document.activeElement).toBe(controls[0]);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Knowledge chat" })).toBeNull();
  // Focus goes back to what opened it.
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /Ask for help/ }));

  const menu = screen.getByRole("button", { name: "Show pages and skills" });
  menu.focus();
  fireEvent.click(menu);
  const drawer = await screen.findByRole("dialog", { name: "Pages and skills" });
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Search pages and skills")));
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(drawer.getAttribute("role")).toBeNull();
  expect(document.activeElement).toBe(menu);
});
