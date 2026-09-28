import { useEffect, useRef, useState } from "react";

import type { ConversationEvent } from "./transcript";

// Every event type the backend emits (backend sessions/runtime.py and manager.py).
// A type left out never reaches the page (useConversationEvents.test.ts checks
// that every type the chat handles is here).
export const EVENT_TYPES = [
  "user_message",
  "turn_started",
  "turn_finished",
  "answer_started",
  "answer_delta",
  "answer",
  "reasoning_delta",
  "command_started",
  "command_output",
  "command_finished",
  "tool_call",
  "files_changed",
  "web_search",
  "usage",
  "error",
  "stop_requested",
  "notice",
  "model_status",
  "checkpoint",
  "files_restored",
  "input_attached",
  "input_removed",
  "exported",
  "input_unavailable",
  "approval_requested",
  "approval_answered",
  "approval_withdrawn",
  "helper_answered",
  "plan_approved",
  "plan_not_frozen",
  "provenance",
  "review_started",
  "review",
  "review_finished",
  "trace",
  "turn_done",
  "title_changed",
  "kb_proposal",
  "kb_proposal_updated",
  "kb_suggestion",
  "kb_suggestion_updated",
];

/**
 * Follow a conversation's events live. The browser's EventSource reconnects by
 * itself and resumes after the last event it saw (Last-Event-ID), so nothing
 * is lost or duplicated if the connection drops or the tab sleeps.
 *
 * While `active` is false (a docked chat that's closed but kept, with its
 * draft) the stream is closed; made active again, it opens after the last
 * event seen, so the events in between arrive and none twice.
 */
export function useConversationEvents(conversationId: string | undefined, active = true): ConversationEvent[] {
  const [events, setEvents] = useState<ConversationEvent[]>([]);
  // Another conversation: its events start afresh.
  const [shownFor, setShownFor] = useState(conversationId);
  if (shownFor !== conversationId) {
    setShownFor(conversationId);
    setEvents([]);
  }
  const last = useRef({ id: conversationId, seq: 0 });

  useEffect(() => {
    if (last.current.id !== conversationId) last.current = { id: conversationId, seq: 0 };
    if (!conversationId || !active) return;
    const seen = last.current;
    const source = new EventSource(`/api/conversations/${conversationId}/stream?after=${seen.seq}`);
    const onEvent = (message: MessageEvent<string>) => {
      const payload = JSON.parse(message.data) as { seq: number; data: Record<string, unknown> };
      if (payload.seq <= seen.seq) return;
      seen.seq = payload.seq;
      const event = { seq: payload.seq, type: message.type, data: payload.data };
      setEvents((previous) => [...previous, event]);
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, onEvent);
    return () => source.close();
  }, [conversationId, active]);

  return events;
}
