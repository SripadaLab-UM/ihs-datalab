import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { use } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";
import { ShowQueryContext } from "@/components/chat/provenance";

import { WorkspacePage } from "./WorkspacePage";

// The real side panel; the chat is stubbed to a number's "in the Queries tab"
// link, using what the page gives it to show a query.
vi.mock("@/api/client", () => ({
  api: {
    conversations: vi.fn(),
    checkpoints: vi.fn(),
    files: vi.fn(),
    dataAccessed: vi.fn(),
    health: vi.fn(),
    fileUrl: vi.fn(() => "/x"),
  },
}));
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: () => {
    const showQuery = use(ShowQueryContext);
    return (
      <button type="button" disabled={!showQuery} onClick={() => showQuery?.("q_0001")}>
        in the Queries tab
      </button>
    );
  },
}));

const conversation = { id: "c1", kind: "data", mode: "analysis", title: "Sleep", model: "m", busy: false } as Conversation;

beforeEach(() => {
  vi.mocked(api.conversations).mockResolvedValue([conversation]);
  vi.mocked(api.checkpoints).mockResolvedValue([]);
  vi.mocked(api.files).mockResolvedValue([]);
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
  vi.mocked(api.dataAccessed).mockResolvedValue([
    { id: "q_0001", started_at: "", status: "succeeded", sql_text: "SELECT 1 FROM dual", tables: ["IHS_2025.X"], row_count: 1, elapsed_ms: 1, result_file: null, message: null },
  ] as never);
});

it("opens the side panel's drawer on a narrower window to show a query asked for, focused", async () => {
  // jsdom has no matchMedia: the window counts as narrower than 1280px.
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workspace/c1"]}>
        <Routes>
          <Route path="workspace/:conversationId" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const panel = document.getElementById("conversation-files")!;
  const link = await screen.findByRole("button", { name: "in the Queries tab" });
  expect(link).toBeEnabled();
  expect(panel).toHaveClass("hidden");
  fireEvent.click(link);
  expect(panel).not.toHaveClass("hidden");
  expect(screen.getByRole("button", { name: "Close the files panel" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Queries", selected: true })).toBeInTheDocument();
  const entry = await waitFor(() => {
    const found = document.getElementById("query-q_0001");
    if (!found || document.activeElement !== found) throw new Error("not yet");
    return found;
  });
  expect(panel).toContainElement(entry);
  expect(entry).toHaveClass("border-ink");
});
