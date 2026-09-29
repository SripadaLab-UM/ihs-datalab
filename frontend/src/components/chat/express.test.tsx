// The Express switch: beside Rigor review in a conversation's header (and in
// the docked chats), never on together with it, Quick effort by default while
// it's on, and an "Express" label on the answers asked with it.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";
import type { FileProvenance } from "@/api/provenance";
import { HowWasThisMade } from "@/features/workspace/HowWasThisMade";

import { Chat } from "./Chat";
import { buildTranscript, type ConversationEvent } from "./transcript";

const stream = vi.hoisted(() => {
  let events: ConversationEvent[] = [];
  const listeners = new Set<() => void>();
  return {
    get: () => events,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    push: (type: string, data: Record<string, unknown> = {}) => {
      events = [...events, { seq: events.length + 1, type, data }];
      listeners.forEach((l) => l());
    },
    reset: () => {
      events = [];
    },
  };
});
vi.mock("./useConversationEvents", () => ({
  useConversationEvents: () => useSyncExternalStore(stream.subscribe, stream.get),
}));
vi.mock("@/api/client", () => ({
  api: {
    modes: vi.fn(async () => [{ id: "analysis", label: "Analysis", kind: "data", description: "", starters: [] }]),
    models: vi.fn(async () => ({ default: "gpt-5.5", available: ["gpt-5.5"] })),
    conversations: vi.fn(async () => []),
    send: vi.fn(),
    stop: vi.fn(),
    files: vi.fn(async () => []),
    setExpress: vi.fn(),
    setRigorReview: vi.fn(),
  },
}));

Element.prototype.scrollIntoView = vi.fn();

const base: Conversation = {
  id: "c1", kind: "data", mode: "analysis", title: "Steps", model: "gpt-5.5",
  created_at: "", updated_at: "", rigor_review: true, express: false, busy: false,
}; // prettier-ignore

function show(conversation: Conversation, assistant?: "sql" | "pipelines") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (c: Conversation) => (
    <QueryClientProvider client={client}>
      <Chat conversation={c} assistant={assistant} />
    </QueryClientProvider>
  );
  const rendered = render(view(conversation));
  return { ...rendered, rerender: (c: Conversation) => rendered.rerender(view(c)) };
}

const express = () => screen.getByRole("checkbox", { name: "Express" });
const rigor = () => screen.getByRole("checkbox", { name: "Rigor review" });
const effort = () => screen.getByLabelText("How hard the agent thinks") as HTMLSelectElement;

async function send(text: string) {
  fireEvent.change(screen.getByLabelText("Your question or instruction"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.send).toHaveBeenCalled());
  const [, , sent] = vi.mocked(api.send).mock.calls.at(-1)!;
  act(() => stream.push("user_message", { text }));
  act(() => stream.push("turn_done"));
  return sent;
}

beforeEach(() => {
  stream.reset();
  vi.mocked(api.send).mockReset().mockResolvedValue(base);
  vi.mocked(api.setExpress).mockReset();
  vi.mocked(api.setRigorReview).mockReset();
  localStorage.clear();
});

it("sits beside Rigor review, off in a new conversation, and switches from the keyboard too", async () => {
  vi.mocked(api.setExpress).mockResolvedValue({ ...base, express: true, rigor_review: false });
  const { rerender } = show(base);
  expect(express()).not.toBeChecked();
  expect(rigor()).toBeChecked();
  // A real checkbox (drawn as a switch): focusable, and Space toggles it natively.
  express().focus();
  expect(document.activeElement).toBe(express());
  fireEvent.click(express());
  await waitFor(() => expect(api.setExpress).toHaveBeenCalledWith("c1", true));
  // DataLab switched the rigor review off: the header shows what it says.
  rerender({ ...base, express: true, rigor_review: false });
  expect(express()).toBeChecked();
  expect(rigor()).not.toBeChecked();
  vi.mocked(api.setRigorReview).mockResolvedValue({ ...base, express: false, rigor_review: true });
  fireEvent.click(rigor());
  await waitFor(() => expect(api.setRigorReview).toHaveBeenCalledWith("c1", true));
  rerender(base);
  expect(express()).not.toBeChecked();
  expect(rigor()).toBeChecked();
  // What it means, from the glossary.
  expect(screen.getAllByText(/low effort, no plans or confirmations/).length).toBeGreaterThan(0);
});

it("thinks at Quick while it's on, unless the person picks another", async () => {
  localStorage.setItem("datalab.effort", "high");
  const { rerender } = show({ ...base, express: true, rigor_review: false });
  expect(effort().value).toBe("low");
  expect(await send("How many interns?")).toBe("low");
  fireEvent.change(effort(), { target: { value: "medium" } });
  expect(await send("And in 2024?")).toBe("medium");
  // Off again: the person's usual choice, which Express didn't change.
  rerender({ ...base, express: false, rigor_review: false });
  expect(effort().value).toBe("high");
  expect(localStorage.getItem("datalab.effort")).toBe("high");
  // On again: Quick again.
  rerender({ ...base, express: true, rigor_review: false });
  expect(effort().value).toBe("low");
});

it("labels the answers asked with Express, and only those", () => {
  show(base);
  act(() => {
    stream.push("user_message", { text: "Quick one?", express: true });
    stream.push("answer", { id: "a1", text: "12 interns.", phase: "final_answer" });
    stream.push("turn_finished", { status: "completed" });
    stream.push("turn_done");
    stream.push("user_message", { text: "Slow one?" });
    stream.push("answer", { id: "a2", text: "Here's the plan.", phase: "final_answer" });
    stream.push("turn_finished", { status: "completed" });
    stream.push("turn_done");
  });
  const [quick, slow] = screen.getAllByRole("article");
  expect(within(quick).getByText("Express")).toBeTruthy();
  expect(within(slow).queryByText("Express")).toBeNull();
  expect(buildTranscript(stream.get()).map((t) => t.express)).toEqual([true, false]);
});

it("is in the docked chats too, where there's no rigor review", () => {
  show({ ...base, mode: "sql", rigor_review: false }, "sql");
  expect(express()).not.toBeChecked();
  expect(screen.queryByRole("checkbox", { name: "Rigor review" })).toBeNull();
});

it("says in How was this made? that the turn was an Express one", () => {
  const made: FileProvenance = {
    path: "outputs/count.csv", found: true, summary: "Written in turn 1.", checkpoint: 1, turn: 1,
    express: true, in_review: false, turn_not_saved: false, commands: [], more_commands: 0,
    edited_directly: false, scripts: [], queries: [], more_queries: 0,
  }; // prettier-ignore
  const { rerender } = render(<HowWasThisMade provenance={made} />);
  expect(screen.getByText(/Turn 1 was asked with Express on/)).toBeTruthy();
  rerender(<HowWasThisMade provenance={{ ...made, express: false }} />);
  expect(screen.queryByText(/Express/)).toBeNull();
});
