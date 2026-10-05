// A click on a starter or Send shows at once: the message, and a line saying
// it's on its way, until DataLab's first event for it arrives. A double click
// sends once; a failure puts the text back. The same in the docked chats.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { Chat } from "./Chat";
import { DockedChat } from "./DockedChat";
import { SLOW_MS } from "./Pending";
import type { ConversationEvent } from "./transcript";

// The conversation's events, pushed by each test as DataLab would stream them.
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
    modes: vi.fn(async () => [
      { id: "analysis", label: "Analysis", kind: "data", description: "", starters: ["How did steps change?"], remember: true },
    ]),
    models: vi.fn(async () => ({ default: "gpt-5.5", available: ["gpt-5.5"] })),
    conversations: vi.fn(async () => []),
    createConversation: vi.fn(),
    send: vi.fn(),
    stop: vi.fn(),
    files: vi.fn(async () => []),
  },
}));

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: { status: vi.fn(async () => ({ available: true })) },
}));

// jsdom has no layout, so no scrolling.
Element.prototype.scrollIntoView = vi.fn();

const conversation: Conversation = {
  id: "c1", kind: "data", mode: "analysis", title: "New conversation", model: "gpt-5.5",
  created_at: "", updated_at: "", rigor_review: false, express: false, busy: false,
}; // prettier-ignore

/** A promise the test settles when it chooses. */
function later<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function showChat() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Chat conversation={conversation} />
    </QueryClientProvider>,
  );
}

const box = () => screen.getByLabelText("Your question or instruction") as HTMLTextAreaElement;
const sendButton = () => screen.getByRole("button", { name: /Send/ });

beforeEach(() => {
  stream.reset();
  vi.mocked(api.send).mockReset();
  vi.mocked(api.createConversation).mockReset();
});
afterEach(() => vi.useRealTimers());

it("shows a starter at once, with a line that clears on the first event", async () => {
  const sent = later<Conversation>();
  vi.mocked(api.send).mockReturnValue(sent.promise);
  showChat();
  fireEvent.click(await screen.findByText("How did steps change?"));
  // Before DataLab has answered anything.
  expect(screen.getByTestId("pending-message")).toHaveTextContent("How did steps change?");
  expect(screen.getByRole("status")).toHaveTextContent("Sending…");
  expect(screen.queryByText("Try one of these, or ask your own")).toBeNull();
  expect(sendButton()).toBeDisabled();
  await act(async () => sent.resolve(conversation));
  // Still shown until the message's own event arrives.
  expect(screen.getByTestId("pending-message")).toBeTruthy();
  act(() => stream.push("user_message", { text: "How did steps change?" }));
  expect(screen.queryByTestId("pending-message")).toBeNull();
  expect(screen.getByRole("heading", { name: "How did steps change?" })).toBeTruthy();
});

it("says what's happening while the sandbox starts, then what the agent is doing", async () => {
  vi.mocked(api.send).mockResolvedValue(conversation);
  showChat();
  fireEvent.click(await screen.findByText("How did steps change?"));
  act(() => stream.push("user_message", { text: "How did steps change?" }));
  expect(await screen.findByText("Starting the agent's sandbox…")).toBeTruthy();
  act(() => stream.push("turn_started", { turn_id: "t1" }));
  expect(screen.queryByText("Starting the agent's sandbox…")).toBeNull();
  expect(screen.getByText("Getting started…")).toBeTruthy();
});

it("says why it's taking a while after a few seconds, and never more", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.send).mockReturnValue(new Promise(() => undefined));
  showChat();
  fireEvent.click(await screen.findByText("How did steps change?"));
  expect(screen.getByRole("status")).toHaveTextContent("Sending…");
  act(() => vi.advanceTimersByTime(SLOW_MS + 10));
  expect(screen.getByRole("status")).toHaveTextContent("DataLab is preparing the workspace before the agent starts…");
});

it("sends a starter once however often it's clicked", async () => {
  vi.mocked(api.send).mockReturnValue(new Promise(() => undefined));
  showChat();
  const starter = await screen.findByText("How did steps change?");
  fireEvent.click(starter);
  fireEvent.click(starter);
  await waitFor(() => expect(api.send).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 50));
  expect(api.send).toHaveBeenCalledTimes(1);
});

it("clears the box at once, and puts the text back with the error if it fails", async () => {
  const sent = later<Conversation>();
  vi.mocked(api.send).mockReturnValue(sent.promise);
  showChat();
  await screen.findByText("How did steps change?");
  fireEvent.change(box(), { target: { value: "Steps by month?" } });
  fireEvent.click(sendButton());
  fireEvent.click(sendButton());
  expect(box().value).toBe("");
  await waitFor(() => expect(api.send).toHaveBeenCalledTimes(1));
  expect(screen.getByTestId("pending-message")).toHaveTextContent("Steps by month?");
  await act(async () => sent.reject(new Error("The agent is still working on the previous message.")));
  expect(screen.queryByTestId("pending-message")).toBeNull();
  expect(box().value).toBe("Steps by month?");
  expect(screen.getByText("The agent is still working on the previous message.")).toBeTruthy();
});

it("puts a starter that couldn't be sent in the box", async () => {
  vi.mocked(api.send).mockRejectedValue(new Error("DataLab couldn't be reached."));
  showChat();
  fireEvent.click(await screen.findByText("How did steps change?"));
  await waitFor(() => expect(box().value).toBe("How did steps change?"));
  expect(screen.getByText("DataLab couldn't be reached.")).toBeTruthy();
});

it("does the same in a docked chat, while the conversation is created", async () => {
  const created = later<Conversation>();
  vi.mocked(api.createConversation).mockReturnValue(created.promise);
  vi.mocked(api.send).mockResolvedValue(conversation);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DockedChat mode="analysis" />
    </QueryClientProvider>,
  );
  const starter = await screen.findByText("How did steps change?");
  fireEvent.click(starter);
  fireEvent.click(starter);
  expect(screen.getByTestId("pending-message")).toHaveTextContent("How did steps change?");
  expect(screen.getByRole("status")).toHaveTextContent("Sending…");
  await act(async () => created.resolve(conversation));
  expect(api.createConversation).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(api.send).toHaveBeenCalledTimes(1));
  // The new conversation keeps showing it until its event arrives: no empty state in between.
  expect(await screen.findByRole("button", { name: /rename/ })).toBeTruthy();
  expect(screen.getByTestId("pending-message")).toBeTruthy();
  act(() => stream.push("user_message", { text: "How did steps change?" }));
  expect(screen.queryByTestId("pending-message")).toBeNull();
});

it("puts the text back in a docked chat whose conversation couldn't be started", async () => {
  vi.mocked(api.createConversation).mockRejectedValue(new Error("DataLab couldn't be reached."));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DockedChat mode="analysis" />
    </QueryClientProvider>,
  );
  await screen.findByText("How did steps change?");
  fireEvent.change(box(), { target: { value: "Sleep by cohort?" } });
  fireEvent.click(sendButton());
  expect(box().value).toBe("");
  expect(screen.getByTestId("pending-message")).toBeTruthy();
  await waitFor(() => expect(box().value).toBe("Sleep by cohort?"));
  expect(screen.queryByTestId("pending-message")).toBeNull();
  expect(screen.getByText("DataLab couldn't be reached.")).toBeTruthy();
  // The starters are back, to pick again.
  expect(screen.getByText("How did steps change?")).toBeTruthy();
});

it("holds Send after DataLab has the message, until its event arrives", async () => {
  vi.mocked(api.send).mockResolvedValue(conversation);
  showChat();
  await screen.findByText("How did steps change?");
  fireEvent.change(box(), { target: { value: "Steps by month?" } });
  fireEvent.click(sendButton());
  await waitFor(() => expect(api.send).toHaveBeenCalledTimes(1));
  // The POST is done, but the message's event hasn't come: a second one would be refused.
  await act(async () => undefined);
  fireEvent.change(box(), { target: { value: "And by week?" } });
  expect(sendButton()).toBeDisabled();
  fireEvent.keyDown(box(), { key: "Enter" });
  expect(api.send).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("pending-message")).toHaveTextContent("Steps by month?");
  act(() => stream.push("user_message", { text: "Steps by month?" }));
  expect(screen.queryByTestId("pending-message")).toBeNull();
});

it("gives a failed message back as typed, before anything typed since", async () => {
  const sent = later<Conversation>();
  vi.mocked(api.send).mockReturnValue(sent.promise);
  showChat();
  await screen.findByText("How did steps change?");
  fireEvent.change(box(), { target: { value: "  Steps by month?  " } });
  fireEvent.click(sendButton());
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", "Steps by month?", "medium"));
  fireEvent.change(box(), { target: { value: "and by week" } });
  await act(async () => sent.reject(new Error("DataLab couldn't be reached.")));
  expect(box().value).toBe("  Steps by month?  \nand by week");
});

it("Remember in Knowledge goes through the chat's own send: shown at once, and marked for the agent", async () => {
  const sent = later<Conversation>();
  vi.mocked(api.send).mockReturnValue(sent.promise);
  showChat();
  const remember = await screen.findByRole("button", { name: /Remember in Knowledge/ });
  await waitFor(() => expect(remember).toBeEnabled());
  fireEvent.click(remember);
  fireEvent.change(await screen.findByLabelText("What should DataLab remember?"), {
    target: { value: "Zero steps means the tracker wasn't synced." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask the agent" }));
  await waitFor(() =>
    expect(api.send).toHaveBeenCalledWith("c1", "Zero steps means the tracker wasn't synced.", "medium", true),
  );
  expect(screen.getByTestId("pending-message")).toHaveTextContent("Zero steps means the tracker wasn't synced.");
  await act(async () => sent.resolve(conversation));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  // One message at a time: nothing else until it's in.
  fireEvent.change(box(), { target: { value: "And by week?" } });
  expect(sendButton()).toBeDisabled();
  act(() => stream.push("user_message", { text: "Zero steps means the tracker wasn't synced.", kb_request: true }));
  expect(screen.queryByTestId("pending-message")).toBeNull();
  expect(screen.getByText("Remember in Knowledge", { selector: "span, p" })).toBeTruthy();
});
