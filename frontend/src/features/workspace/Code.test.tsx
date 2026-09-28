import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";
import { type CodeFile, type CodeListing, codeApi } from "@/api/code";
import { SHOW_STEP } from "@/components/chat/showStep";

import { CodePanel } from "./Code";
import { SidePanel } from "./SidePanel";

vi.mock("@/api/client", () => ({
  api: { files: vi.fn(), health: vi.fn(), fileUrl: vi.fn(() => "/x") },
}));
vi.mock("@/api/code", () => ({ codeApi: { list: vi.fn(), version: vi.fn(), diff: vi.fn() } }));

const version = (checkpoint: number, turn = checkpoint) => ({
  checkpoint,
  turn,
  created_at: "2026-09-28T14:41:00+00:00",
  label: `After turn ${turn}`,
  size: 20,
  too_large: false,
});
const file = (path: string, status: CodeFile["status"], checkpoints: number[], language = "r"): CodeFile => ({
  path,
  language,
  status,
  size: 20,
  current: status !== "deleted",
  versions: checkpoints.map((n) => (n === 0 ? { ...version(0), turn: null, created_at: "", label: "As copied into the workspace" } : version(n))),
});
const listing: CodeListing = {
  files: [
    file("scripts/steps_by_week.R", "new", [1, 2]),
    file("pipes/R/steps.R", "modified", [0, 2]),
    file("query.sql", "new", [1], "sql"),
    file("steps.ipynb", "new", [1], "notebook"),
  ],
  inline: [
    { id: "c9", step: "cmd-c9", turn: 2, language: "python", code: "import pandas as pd\nprint(1)", truncated: false, exit_code: 0 },
  ],
  unchanged: 3,
  more: 0,
  latest_checkpoint: 2,
};

const wrap = (node: ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);

beforeEach(() => {
  vi.mocked(codeApi.list).mockReset().mockResolvedValue(listing);
  vi.mocked(codeApi.version).mockReset().mockImplementation(async (_id, path, checkpoint) => ({
    path,
    language: path.endsWith(".ipynb") ? "python" : "r",
    version: version(checkpoint ?? 2),
    current: checkpoint === 2,
    too_large: false,
    text: path.endsWith(".ipynb") ? null : `x <- ${checkpoint}`,
    notebook: path.endsWith(".ipynb")
      ? {
          language: "python",
          outputs: 3,
          cells: [
            { kind: "markdown", source: "# Weekly steps", outputs: 0 },
            { kind: "code", source: "import pandas as pd", outputs: 3 },
          ],
        }
      : null,
    unreadable: false,
  }));
  vi.mocked(codeApi.diff).mockReset().mockResolvedValue({
    path: "scripts/steps_by_week.R",
    language: "r",
    base: version(1),
    head: version(2),
    head_current: true,
    too_large: false,
    base_text: "x <- 1",
    head_text: "x <- 2",
    lines: [
      { op: "@", old: null, new: null, text: "@@ -1,1 +1,1 @@" },
      { op: "-", old: 1, new: null, text: "x <- 1" },
      { op: "+", old: null, new: 1, text: "x <- 2" },
    ],
    added: 1,
    removed: 1,
    truncated: false,
  });
});

it("lists the conversation's code by status, newest first, with its language and last turn", async () => {
  wrap(<CodePanel conversationId="c1" />);
  const fresh = (await screen.findByRole("heading", { name: "New" })).closest("section")!;
  const rows = within(fresh).getAllByRole("button");
  expect(rows.map((row) => row.getAttribute("title"))).toEqual(["scripts/steps_by_week.R", "query.sql", "steps.ipynb"]);
  expect(rows[0]).toHaveTextContent("steps_by_week.R");
  expect(rows[0]).toHaveTextContent("new");
  expect(rows[0]).toHaveTextContent("R · turn 2 · 2 versions");
  expect(rows[1]).toHaveTextContent("SQL · turn 1");
  const modified = screen.getByRole("heading", { name: "Modified" }).closest("section")!;
  expect(within(modified).getByRole("button")).toHaveTextContent("modified");
  expect(screen.getByRole("heading", { name: "Inline code" })).toBeInTheDocument();
  // Unchanged repository copies are left out, and can be shown.
  const all = screen.getByRole("checkbox", { name: /Show 3 unchanged repository files/ });
  fireEvent.click(all);
  await waitFor(() => expect(codeApi.list).toHaveBeenLastCalledWith("c1", true));
});

it("opens a file at its current version, labelled as the current file, and older ones as snapshots", async () => {
  wrap(<CodePanel conversationId="c1" />);
  fireEvent.click(await screen.findByTitle("scripts/steps_by_week.R"));
  const dialog = await screen.findByRole("dialog");
  const label = await within(dialog).findByTestId("code-version-label");
  expect(label).toHaveTextContent("Current file in the workspace");
  expect(label).not.toHaveTextContent("snapshot");
  await waitFor(() => expect(codeApi.version).toHaveBeenLastCalledWith("c1", "scripts/steps_by_week.R", 2));
  expect(await within(dialog).findByText("2", { selector: ".tok-number" })).toBeInTheDocument();

  const picker = within(dialog).getByRole("combobox", { name: "Version" });
  expect([...(picker as HTMLSelectElement).options].map((o) => o.text)).toEqual([
    expect.stringMatching(/^Turn 2 · .+ \(current\)$/),
    expect.stringMatching(/^Turn 1 · /),
  ]);
  fireEvent.change(picker, { target: { value: "1" } });
  expect(within(dialog).getByTestId("code-version-label")).toHaveTextContent(/Saved at turn 1 · checkpoint .+ · snapshot, not the live file/);
  await waitFor(() => expect(codeApi.version).toHaveBeenLastCalledWith("c1", "scripts/steps_by_week.R", 1));
  // The first version has nothing before it.
  expect(within(dialog).getByRole("button", { name: "Diff with previous" })).toBeDisabled();
});

it("shows what changed from the previous version, or from another one", async () => {
  wrap(<CodePanel conversationId="c1" />);
  fireEvent.click(await screen.findByTitle("scripts/steps_by_week.R"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(await within(dialog).findByRole("button", { name: "Diff with previous" }));
  expect(await within(dialog).findByText(/Changes from Turn 1 · .+ to Turn 2/)).toBeInTheDocument();
  expect(codeApi.diff).toHaveBeenCalledWith("c1", "scripts/steps_by_week.R", 1, 2);
  expect(within(dialog).getByRole("table")).toHaveTextContent("x <- 1");
  await waitFor(() => expect(dialog.querySelector("[data-op=added] .tok-number")).toHaveTextContent("2"));
  fireEvent.click(within(dialog).getByRole("button", { name: "Show the code" }));
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Diff with another version" }), { target: { value: "1" } });
  expect(await within(dialog).findByRole("table")).toBeInTheDocument();
});

it("labels a repository file's first version as the copy DataLab made", async () => {
  wrap(<CodePanel conversationId="c1" />);
  fireEvent.click(await screen.findByTitle("pipes/R/steps.R"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(await within(dialog).findByRole("combobox", { name: "Version" }), { target: { value: "0" } });
  expect(within(dialog).getByTestId("code-version-label")).toHaveTextContent(
    "As copied into the workspace before the first turn · snapshot, not the live file",
  );
});

it("shows a notebook's cells and never its outputs", async () => {
  wrap(<CodePanel conversationId="c1" />);
  fireEvent.click(await screen.findByTitle("steps.ipynb"));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText(/3 outputs not shown: a notebook/)).toBeInTheDocument();
  expect(within(dialog).getByText("# Weekly steps")).toBeInTheDocument();
  await waitFor(() => expect(dialog.querySelector("[data-cell=code] .tok-keyword")).toHaveTextContent("import"));
});

it("links inline code to its step in the chat's activity", async () => {
  const shown: string[] = [];
  const listen = (event: Event) => shown.push((event as CustomEvent<{ key: string }>).detail.key);
  window.addEventListener(SHOW_STEP, listen);
  wrap(<CodePanel conversationId="c1" />);
  const group = (await screen.findByRole("heading", { name: "Inline code" })).closest("section")!;
  expect(within(group).getByText("Python snippet")).toBeInTheDocument();
  expect(within(group).getByText("· turn 2")).toBeInTheDocument();
  await waitFor(() => expect(group.querySelector(".tok-keyword")).toHaveTextContent("import"));
  fireEvent.click(within(group).getByRole("button", { name: /Show in the activity/ }));
  window.removeEventListener(SHOW_STEP, listen);
  expect(shown).toEqual(["cmd-c9"]);
});

it("says when there's no code yet", async () => {
  vi.mocked(codeApi.list).mockResolvedValue({ files: [], inline: [], unchanged: 0, more: 0, latest_checkpoint: null });
  wrap(<CodePanel conversationId="c1" />);
  expect(await screen.findByText("No code yet")).toBeInTheDocument();
});

it("keeps code out of Outputs, with a link to the Code tab", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
  vi.mocked(api.files).mockResolvedValue([
    { path: "report.html", size: 10, kind: "html", modified: "", checkpoint: 2 },
    { path: "steps.csv", size: 10, kind: "csv", modified: "", checkpoint: 2 },
    { path: "analysis.R", size: 10, kind: "text", modified: "", checkpoint: 2 },
    { path: "extract.sql", size: 10, kind: "text", modified: "", checkpoint: 2 },
  ]);
  const conversation = { id: "c1", kind: "data", mode: "analysis", title: "t", model: "m", busy: false } as Conversation;
  wrap(<SidePanel conversation={conversation} onOpen={() => {}} />);
  expect(await screen.findByText("2 files in outputs")).toBeInTheDocument();
  expect(screen.getByTitle("report.html")).toBeInTheDocument();
  expect(screen.queryByTitle("analysis.R")).toBeNull();
  expect(screen.queryByTitle("extract.sql")).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: /4 code files in the Code tab/ }));
  expect(screen.getByRole("tab", { name: "Code", selected: true })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "New" })).toBeInTheDocument();
});
