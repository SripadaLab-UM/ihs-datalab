import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation, type WorkspaceFile } from "@/api/client";

import { ExportDialog } from "./ExportDialog";
import { FileViewer } from "./FileViewer";

// From the Milestone 3 review: what's exported, and what a preview shows,
// must be the version the person was shown, not whatever is newest.
vi.mock("@/api/client", () => ({
  api: {
    files: vi.fn(),
    destinations: vi.fn(),
    health: vi.fn(),
    checkpoints: vi.fn(),
    export: vi.fn(),
    preview: vi.fn(),
  },
}));
vi.mock("./report", () => ({ buildReport: vi.fn(), REPORT_CSS: "" }));

const conversation = { id: "c", title: "Review", kind: "data", mode: "analysis", model: "m", busy: false } as Conversation;
const listed = (checkpoint: number): WorkspaceFile[] => [
  { path: "report.html", size: 100, kind: "html", modified: "", checkpoint },
];
const checkpoint = (number: number) => ({
  number,
  label: `Checkpoint ${number}`,
  created_at: "",
  files: 1,
  bytes: 100,
  skipped: [],
  turn: number,
});
const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });

beforeEach(() => {
  vi.mocked(api.destinations).mockResolvedValue([{ id: "d", name: "Folder", path: "/x", available: true }] as never);
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  vi.mocked(api.export).mockReset().mockResolvedValue({ folder: "/x", files: ["report.html"] } as never);
  vi.mocked(api.preview).mockReset();
});

async function openDialogThenNewerFiles() {
  const client = newClient();
  vi.mocked(api.files).mockResolvedValue(listed(1));
  render(
    <QueryClientProvider client={client}>
      <ExportDialog conversation={conversation} withReport={false} onClose={() => {}} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByLabelText(/report.html/));
  // The agent saves a new version of the same file while the dialog is open.
  await act(async () => {
    client.setQueryData(["files", "c"], listed(2));
  });
  expect(await screen.findByRole("status")).toHaveTextContent("newer files");
}

it("exports the version the person chose, even when a newer one arrives", async () => {
  await openDialogThenNewerFiles();
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  await waitFor(() => expect(api.export).toHaveBeenCalled());
  expect(vi.mocked(api.export).mock.calls.at(-1)?.[4]).toBe(1);
});

it("taking the newer files clears the choice, so nothing is exported unchosen", async () => {
  await openDialogThenNewerFiles();
  fireEvent.click(screen.getByRole("button", { name: "Show the newer files" }));
  expect(screen.getByLabelText(/report.html/)).not.toBeChecked();
  expect(screen.getByTestId("export-version")).toHaveTextContent("checkpoint 2");
});

it("a reopened preview shows the file as listed now, and an open one stays on its version", async () => {
  const client = newClient();
  vi.mocked(api.checkpoints).mockResolvedValue([checkpoint(1)]);
  vi.mocked(api.preview).mockImplementation(async (_c, _r, _p, number) => ({
    url: `/preview/t${number}/report.html`,
    checkpoint: number ?? 0,
  }));
  const viewer = (number: number) => (
    <QueryClientProvider client={client}>
      <FileViewer conversationId="c" file={{ root: "outputs", path: "report.html", kind: "html", checkpoint: number }} onClose={() => {}} />
    </QueryClientProvider>
  );
  const first = render(viewer(1));
  await waitFor(() => expect(screen.getByTitle("report.html")).toHaveAttribute("src", "/preview/t1/report.html"));
  await waitFor(() => expect(client.getQueryData(["checkpoints", "c"])).toBeDefined());
  // A new checkpoint while the viewer is open: it says so, and keeps its version.
  await act(async () => {
    client.setQueryData(["checkpoints", "c"], [checkpoint(2), checkpoint(1)]);
  });
  await waitFor(() => expect(screen.getByTestId("file-version")).toHaveTextContent("A newer version"));
  expect(screen.getByTitle("report.html")).toHaveAttribute("src", "/preview/t1/report.html");
  first.unmount();

  // Reopened from the listing, which now lists checkpoint 2.
  render(viewer(2));
  await waitFor(() => expect(screen.getByTitle("report.html")).toHaveAttribute("src", "/preview/t2/report.html"));
  expect(vi.mocked(api.preview).mock.calls.map((call) => call[3])).toEqual([1, 2]);
});

it("a file opened from a link in an answer is pinned to the latest checkpoint", async () => {
  const client = newClient();
  vi.mocked(api.checkpoints).mockResolvedValue([checkpoint(3)]);
  vi.mocked(api.preview).mockImplementation(async (_c, _r, _p, number) => ({ url: `/preview/t${number}/r.html`, checkpoint: 3 }));
  render(
    <QueryClientProvider client={client}>
      <FileViewer conversationId="c" file={{ root: "outputs", path: "r.html", kind: "html" }} onClose={() => {}} />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTitle("r.html")).toHaveAttribute("src", "/preview/t3/r.html"));
  expect(vi.mocked(api.preview).mock.calls.map((call) => call[3])).toEqual([3]);
});

// The user's call: each script can go with the outputs, but only when ticked.
function openWithScripts() {
  const client = newClient();
  vi.mocked(api.files).mockImplementation(async (_id, root) =>
    root === "work"
      ? [
          { path: "outputs/report.html", size: 100, kind: "html", modified: "", checkpoint: 3 },
          { path: "scripts/steps_by_week.R", size: 20, kind: "text", modified: "", checkpoint: 3 },
          { path: "scripts/explore.ipynb", size: 30, kind: "text", modified: "", checkpoint: 3 },
          { path: "scripts/notes.txt", size: 5, kind: "text", modified: "", checkpoint: 3 },
          { path: "joined.csv", size: 5, kind: "csv", modified: "", checkpoint: 3 },
          { path: "explore.py", size: 5, kind: "text", modified: "", checkpoint: 3 },
        ]
      : [{ path: "report.html", size: 100, kind: "html", modified: "", checkpoint: 3 }],
  );
  render(
    <QueryClientProvider client={client}>
      <ExportDialog conversation={conversation} withReport={false} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

it("offers each script in scripts/, none ticked, and exports only those picked", async () => {
  openWithScripts();
  const script = await screen.findByRole("checkbox", { name: /scripts\/steps_by_week\.R/ });
  const notebook = screen.getByRole("checkbox", { name: /scripts\/explore\.ipynb/ });
  expect(script).not.toBeChecked();
  expect(notebook).not.toBeChecked();
  expect(screen.getByText(/without outputs/)).toBeInTheDocument();
  // Only code in scripts/: not notes, not data, not code elsewhere in /work.
  expect(screen.queryByText("scripts/notes.txt")).toBeNull();
  expect(screen.queryByText("joined.csv")).toBeNull();
  expect(screen.queryByText("explore.py")).toBeNull();
  // Nothing picked: nothing to export.
  expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  fireEvent.click(script);
  const button = screen.getByRole("button", { name: "Export" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(api.export).toHaveBeenCalled());
  const [, , files, , shown] = vi.mocked(api.export).mock.calls.at(-1)!;
  expect(files).toEqual([{ root: "work", path: "scripts/steps_by_week.R" }]);
  expect(shown).toBe(3);
});

it("includes all scripts at once, and none again", async () => {
  openWithScripts();
  const all = await screen.findByRole("checkbox", { name: "Include all scripts" });
  expect(all).not.toBeChecked();
  fireEvent.click(all);
  expect(screen.getByRole("checkbox", { name: /steps_by_week\.R/ })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /explore\.ipynb/ })).toBeChecked();
  fireEvent.click(all);
  expect(screen.getByRole("checkbox", { name: /steps_by_week\.R/ })).not.toBeChecked();
  fireEvent.click(all);
  fireEvent.click(screen.getByLabelText(/report.html/));
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  await waitFor(() => expect(api.export).toHaveBeenCalled());
  expect(vi.mocked(api.export).mock.calls.at(-1)?.[2]).toEqual([
    { root: "outputs", path: "report.html" },
    { root: "work", path: "scripts/steps_by_week.R" },
    { root: "work", path: "scripts/explore.ipynb" },
  ]);
});

it("says a notebook in outputs is exported without its outputs", async () => {
  vi.mocked(api.files).mockImplementation(async (_id, root) =>
    root === "work" ? [] : [{ path: "explore.ipynb", size: 30, kind: "text", modified: "", checkpoint: 4 }],
  );
  render(
    <QueryClientProvider client={newClient()}>
      <ExportDialog conversation={conversation} withReport={false} onClose={() => {}} />
    </QueryClientProvider>,
  );
  const notebook = await screen.findByRole("checkbox", { name: /explore\.ipynb.*without outputs/ });
  expect(notebook).not.toBeChecked();
});
