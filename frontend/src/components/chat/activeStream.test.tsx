// A docked chat that's closed but kept: its stream closes, and reopens where it left off.
import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { useConversationEvents } from "./useConversationEvents";

class FakeEventSource {
  static all: FakeEventSource[] = [];
  listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {};
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }
  addEventListener(type: string, listener: (e: MessageEvent<string>) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, seq: number) {
    for (const listener of this.listeners[type] ?? []) listener({ type, data: JSON.stringify({ seq, data: {} }) } as MessageEvent<string>);
  }
}

beforeEach(() => {
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

it("closes the stream while hidden, and reopens it after the last event seen, with no gap or repeat", () => {
  const { result, rerender } = renderHook(({ active }) => useConversationEvents("c1", active), { initialProps: { active: true } });
  const first = FakeEventSource.all[0];
  expect(first.url).toBe("/api/conversations/c1/stream?after=0");
  act(() => {
    first.emit("user_message", 1);
    first.emit("answer", 2);
  });
  expect(result.current.map((e) => e.seq)).toEqual([1, 2]);

  rerender({ active: false });
  expect(first.closed).toBe(true);
  expect(FakeEventSource.all).toHaveLength(1);
  // The events are kept while it's hidden.
  expect(result.current.map((e) => e.seq)).toEqual([1, 2]);

  rerender({ active: true });
  const second = FakeEventSource.all[1];
  expect(second.url).toBe("/api/conversations/c1/stream?after=2");
  act(() => {
    second.emit("answer", 2); // already seen: not shown twice
    second.emit("turn_done", 3);
  });
  expect(result.current.map((e) => e.seq)).toEqual([1, 2, 3]);
});

it("starts afresh for another conversation", () => {
  const { result, rerender } = renderHook(({ id }) => useConversationEvents(id), { initialProps: { id: "c1" } });
  act(() => FakeEventSource.all[0].emit("answer", 5));
  rerender({ id: "c2" });
  expect(FakeEventSource.all[0].closed).toBe(true);
  expect(FakeEventSource.all[1].url).toBe("/api/conversations/c2/stream?after=0");
  expect(result.current).toEqual([]);
});
