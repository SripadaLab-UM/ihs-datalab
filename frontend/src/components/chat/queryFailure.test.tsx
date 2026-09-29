import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";

import type { Conversation } from "@/api/client";

import { Chat } from "./Chat";
import { SHOW_QUERY } from "./provenance";
import { showStep } from "./showStep";

// A failed query as the backend's runtime emits it (sessions/runtime.py
// tool_failure, from Codex's isError result), then a query that worked.
const events = vi.hoisted(() => [
  { seq: 1, type: "user_message", data: { text: "Which questions mention sleep?" } },
  {
    seq: 2,
    type: "tool_call",
    data: {
      id: "t1",
      server: "ihs-data",
      tool: "query",
      status: "failed",
      arguments: { sql: "SELECT DISTINCT QUESTIONTEXT FROM IHS_2025.STG_SURVEYDICTIONARY" },
      error:
        "The database refused this query: ORA-00932: inconsistent data types. Often a CLOB (long text) column in SELECT DISTINCT.",
      failure: { category: "sql", code: "ORA-00932", query_id: "q_20260928T120000_abc123" },
      summary: null,
    },
  },
  {
    seq: 3,
    type: "tool_call",
    data: {
      id: "t2",
      server: "ihs-data",
      tool: "query",
      status: "completed",
      arguments: { sql: "SELECT DISTINCT TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000)) FROM IHS_2025.STG_SURVEYDICTIONARY" },
      error: null,
      failure: null,
      summary: { query_id: "q_20260928T120001_abc124", row_count: 12, columns: ["Q"], tables: ["IHS_2025.STG_SURVEYDICTIONARY"], warnings: [] },
    },
  },
  { seq: 4, type: "answer", data: { id: "a", text: "Twelve do.", phase: "final_answer" } },
  { seq: 5, type: "turn_finished", data: { status: "completed" } },
  { seq: 6, type: "turn_done", data: {} },
]);
vi.mock("./useConversationEvents", () => ({ useConversationEvents: () => events }));
vi.mock("@/api/client", () => ({ api: { conversations: vi.fn(async () => []), files: vi.fn(async () => []) } }));
Element.prototype.scrollIntoView = vi.fn();

it("shows why a query failed, that the turn recovered, and links to its Queries entry", async () => {
  const conversation = { id: "c", title: "Sleep", kind: "data", mode: "analysis", model: "m", busy: false } as Conversation;
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <Chat conversation={conversation} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Twelve do.")).toBeTruthy();
  act(() => showStep("tool-t1"));
  expect(await screen.findByText("the database refused it: ORA-00932 — data types don't match (often a long-text column)")).toBeTruthy();
  expect(screen.getByText("recovered")).toBeTruthy();
  expect(screen.getByText("The database refused this query. No result was saved.")).toBeTruthy();
  expect(screen.getByText(/ORA-00932: inconsistent data types\. Often a CLOB/)).toBeTruthy();
  const shown = vi.fn();
  window.addEventListener(SHOW_QUERY, (event) => shown((event as CustomEvent<{ id: string }>).detail.id));
  act(() => screen.getAllByRole("button", { name: /See it in Queries/ })[0].click());
  expect(shown).toHaveBeenCalledWith("q_20260928T120000_abc123");
});
