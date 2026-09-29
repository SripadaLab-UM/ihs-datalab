import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";
import { provenanceApi, type FileProvenance } from "@/api/provenance";
import type { OpenFile } from "@/lib/files";

import { WorkspacePage } from "./WorkspacePage";

// The page's chat and side panel are stubbed: what's under test is how the
// page wires the file viewer, "How was this made?", and opening a script.
vi.mock("@/api/client", () => ({
  api: {
    conversations: vi.fn(),
    checkpoints: vi.fn(),
    fileUrl: vi.fn(() => "/x"),
    fileText: vi.fn(async () => ({ text: "save('outputs/fig1.png')", truncated: false })),
  },
}));
vi.mock("@/api/provenance", () => ({ provenanceApi: { file: vi.fn() } }));
const version = (checkpoint: number) => ({ checkpoint, turn: checkpoint, created_at: "", label: `After turn ${checkpoint}`, size: 10, too_large: false });
vi.mock("@/api/code", () => ({
  codeApi: {
    list: vi.fn(async () => ({
      files: [{ path: "analysis.py", language: "python", status: "new", size: 10, current: true, versions: [version(2), version(3)] }],
      inline: [],
      unchanged: 0,
      more: 0,
      latest_checkpoint: 3,
    })),
    version: vi.fn(async (_id: string, path: string, checkpoint: number) => ({
      path,
      language: "python",
      version: version(checkpoint),
      current: checkpoint === 3,
      too_large: false,
      text: "save('outputs/fig1.png')",
      notebook: null,
    })),
  },
}));
vi.mock("@/components/chat/DockedChat", () => ({ DockedChat: () => null }));
vi.mock("./SidePanel", () => ({
  SidePanel: ({ onOpen }: { onOpen: (file: OpenFile) => void }) => (
    <button type="button" onClick={() => onOpen({ root: "outputs", path: "fig1.png", kind: "image" })}>
      Open fig1.png
    </button>
  ),
}));

const conversation = {
  id: "c1",
  kind: "data",
  mode: "analysis",
  title: "Sleep",
  model: "m",
  busy: false,
} as Conversation;

const made: FileProvenance = {
  path: "outputs/fig1.png",
  found: true,
  summary: "Its current content was first saved in the checkpoint after turn 2 (checkpoint 2).",
  checkpoint: 2,
  turn: 2,
  in_review: false,
  turn_not_saved: false,
  express: false,
  commands: [],
  more_commands: 0,
  edited_directly: false,
  scripts: [{ path: "analysis.py", sha256: "a".repeat(64), names_file: true }],
  queries: [],
  more_queries: 0,
};

beforeEach(() => {
  vi.mocked(api.conversations).mockResolvedValue([conversation]);
  vi.mocked(api.checkpoints).mockResolvedValue([
    { number: 3, label: "After turn 3", created_at: "", files: 2, bytes: 2, skipped: [], turn: 3 },
    { number: 2, label: "After turn 2", created_at: "", files: 2, bytes: 2, skipped: [], turn: 2 },
  ] as never);
  vi.mocked(provenanceApi.file).mockResolvedValue(made);
});

it("opens a file's script from How was this made?, as it was then, in the real workspace", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workspace/c1"]}>
        <Routes>
          <Route path="workspace/:conversationId" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Open fig1.png" }));
  fireEvent.click(await screen.findByRole("button", { name: /How was this made\?/ }));
  expect(await screen.findByText(/first saved in the checkpoint after turn 2/)).toBeInTheDocument();
  expect(provenanceApi.file).toHaveBeenCalledWith("c1", "outputs/fig1.png", 3);
  // The script opens in a fresh viewer, pinned to that checkpoint (not the file's).
  fireEvent.click(screen.getByRole("button", { name: "analysis.py" }));
  // A script opens with its versions, labelled as the snapshot it is.
  expect(await screen.findByTestId("code-version-label")).toHaveTextContent("Saved at turn 2");
  expect(screen.getByTestId("code-version-label")).toHaveTextContent("snapshot, not the live file");
  expect(screen.getByText("analysis.py", { selector: "span.truncate" })).toBeInTheDocument();
});
