import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";

import type { Conversation } from "@/api/client";

import { Chat } from "./Chat";

const events = vi.hoisted(() => [
  { seq: 1, type: "user_message", data: { text: "Write up the wear-time rule." } },
  { seq: 2, type: "command_started", data: { id: "c1", command: "/bin/bash -lc 'cat /work/kb/index.md'" } },
  { seq: 3, type: "command_finished", data: { id: "c1", exit_code: 0, status: "completed" } },
  { seq: 4, type: "answer", data: { id: "a", text: "I proposed a QC page.", phase: "final_answer" } },
  { seq: 5, type: "turn_finished", data: { status: "completed" } },
  {
    seq: 6,
    type: "kb_proposal",
    data: {
      id: "kp_1",
      files: [{ path: "qc/wear-time.md", change: "added", added: 17, removed: 0, flags: [] }],
      refused: [],
      check: { errors: 0, data: 0, warnings: 0 },
    },
  },
  { seq: 7, type: "turn_done", data: {} },
]);
vi.mock("./useConversationEvents", () => ({ useConversationEvents: () => events }));
vi.mock("@/api/client", () => ({ api: { conversations: vi.fn(async () => []), files: vi.fn(async () => []) } }));
vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: { status: vi.fn(() => new Promise(() => {})), proposal: vi.fn(() => new Promise(() => {})) },
}));

Element.prototype.scrollIntoView = vi.fn();

it("shows proposed knowledge edits under the answer, not folded into how it was made", async () => {
  const conversation = { id: "c", title: "Wear time", kind: "data", mode: "extraction", model: "m", busy: false } as Conversation;
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <Chat conversation={conversation} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("I proposed a QC page.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Knowledge edits proposed (1 page)" })).toBeTruthy();
  expect(screen.getByText("How this answer was made")).toBeTruthy();
  // The story is folded; the card isn't.
  expect(screen.queryByText("Read index.md")).toBeNull();
});
