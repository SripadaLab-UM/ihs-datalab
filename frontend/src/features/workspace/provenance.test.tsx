import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";
import { provenanceApi, type FileProvenance } from "@/api/provenance";
import { Markdown } from "@/components/chat/Markdown";
import { showQuery } from "@/components/chat/provenance";
import { buildTranscript } from "@/components/chat/transcript";
import { OpenFileContext } from "@/lib/files";

import { FileViewer } from "./FileViewer";
import { SidePanel } from "./SidePanel";

vi.mock("@/api/client", () => ({
  api: {
    checkpoints: vi.fn(),
    fileUrl: vi.fn(() => "/x"),
    health: vi.fn(),
    files: vi.fn(),
    dataAccessed: vi.fn(),
  },
}));
vi.mock("@/api/provenance", () => ({ provenanceApi: { file: vi.fn() } }));

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });
const conversation = { id: "c1", kind: "data", mode: "analysis", title: "t", model: "m", busy: false } as Conversation;

beforeEach(() => {
  vi.mocked(api.checkpoints).mockResolvedValue([{ number: 2, label: "After turn 2", created_at: "", files: 1, bytes: 1, skipped: [], turn: 2 }] as never);
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
  vi.mocked(api.files).mockResolvedValue([]);
  vi.mocked(api.dataAccessed).mockResolvedValue([
    { id: "q_0001", started_at: "", status: "succeeded", sql_text: "SELECT 1 FROM dual", tables: ["IHS_2025.X"], row_count: 1, elapsed_ms: 1, result_file: null, message: null },
  ] as never);
});

it("keeps each turn's provenance event with the turn", () => {
  let seq = 0;
  const e = (type: string, data: Record<string, unknown> = {}) => ({ seq: ++seq, type, data });
  const [turn] = buildTranscript([
    e("user_message", { text: "Sleep?" }),
    e("answer", { id: "m1", phase: "final_answer", text: "It was 7.2 h." }),
    e("provenance", { answer: "m1", numbers: [{ text: "7.2", sources: [{ kind: "command", ref: "c1" }] }], more_numbers: 0, files: [] }),
  ]);
  expect(turn.provenance?.numbers).toEqual([{ text: "7.2", sources: [{ kind: "command", ref: "c1" }] }]);
});

it("renders an answer's numbers with where they appear, and opens a query", async () => {
  const shown = vi.fn();
  window.addEventListener("datalab:show-query", (event) => shown((event as CustomEvent).detail.id));
  const openFile = vi.fn();
  render(
    <OpenFileContext value={openFile}>
      <Markdown
        text="Mean 7.2 h in 81 interns; 1.3 appears nowhere."
        numbers={{
          sources: new Map([
            ["7.2", [{ kind: "file", ref: "outputs/by_month.csv" }]],
            ["81", [{ kind: "query", ref: "q_0001" }]],
            ["1.3", []],
          ]),
          links: { commandText: () => undefined, openQuery: showQuery, openFile: (path) => openFile(path) },
        }}
      />
    </OpenFileContext>,
  );
  fireEvent.click(screen.getByRole("button", { name: "81" }));
  fireEvent.click(screen.getByRole("button", { name: "in Queries" }));
  expect(shown).toHaveBeenCalledWith("q_0001");
  fireEvent.click(screen.getByRole("button", { name: "7.2" }));
  fireEvent.click(screen.getByRole("button", { name: "outputs/by_month.csv" }));
  expect(openFile).toHaveBeenCalledWith("outputs/by_month.csv");
  expect(screen.getByRole("button", { name: "1.3" })).toHaveClass("text-attn");
});

it("shows a query asked for in the Queries tab", async () => {
  render(
    <QueryClientProvider client={client()}>
      <SidePanel conversation={conversation} onOpen={vi.fn()} />
    </QueryClientProvider>,
  );
  showQuery("q_0001");
  const entry = await waitFor(() => {
    const found = document.getElementById("query-q_0001");
    if (!found) throw new Error("not yet");
    return found;
  });
  expect(entry).toHaveClass("border-ink");
  expect(screen.getByText("SELECT 1 FROM dual")).toBeInTheDocument();
});

const made: FileProvenance = {
  path: "outputs/fig1.png",
  found: true,
  summary: "Its current content was first saved in the checkpoint after turn 2 (checkpoint 2).",
  checkpoint: 2,
  turn: 2,
  in_review: false,
  turn_not_saved: false,
  commands: [{ id: "c2", command: "python analysis.py", exit_code: 0, names_file: false, via_script: "analysis.py", seen_in_output: false }],
  more_commands: 0,
  edited_directly: false,
  scripts: [{ path: "analysis.py", sha256: "a".repeat(64), names_file: true }],
  queries: [],
  more_queries: 0,
};

it("shows how a file in the viewer was made, and opens its script as it was then", async () => {
  vi.mocked(provenanceApi.file).mockResolvedValue(made);
  const openFile = vi.fn();
  render(
    <QueryClientProvider client={client()}>
      <OpenFileContext value={openFile}>
        <FileViewer conversationId="c1" file={{ root: "outputs", path: "fig1.png", kind: "image" }} onClose={vi.fn()} />
      </OpenFileContext>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /How was this made\?/ }));
  expect(await screen.findByText(/first saved in the checkpoint after turn 2/)).toBeInTheDocument();
  expect(provenanceApi.file).toHaveBeenCalledWith("c1", "outputs/fig1.png");
  fireEvent.click(screen.getByRole("button", { name: "analysis.py" }));
  expect(openFile).toHaveBeenCalledWith({ root: "work", path: "analysis.py", kind: "text", checkpoint: 2 });
});

it("doesn't offer it for a query result, which checkpoints don't keep", () => {
  render(
    <QueryClientProvider client={client()}>
      <FileViewer conversationId="c1" file={{ root: "results", path: "q_0001.csv", kind: "csv" }} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(screen.queryByRole("button", { name: /How was this made\?/ })).toBeNull();
});
