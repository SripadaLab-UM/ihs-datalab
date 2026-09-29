// What the chat keeps in step with its conversation's stream: the queries its
// events make stale, and the view following the latest step.
import { useQueryClient } from "@tanstack/react-query";
import { type UIEvent, useEffect, useRef, useState } from "react";

import type { ConversationEvent } from "./transcript";

/** Refresh what the conversation's events change: busy dots and data accessed
 *  when a turn finishes, the side panel's files, and the title. */
export function useRefreshOnEvents(events: ConversationEvent[], running: boolean, conversationId: string) {
  const queryClient = useQueryClient();
  // When a turn finishes, refresh what depends on it (busy dots, data accessed).
  useEffect(() => {
    if (!running) {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["data-accessed", conversationId] });
    }
  }, [running, conversationId, queryClient]);

  // A new checkpoint or a restore changes the files the side panel shows.
  const lastFilesEvent = events.findLast((e) =>
    ["checkpoint", "files_restored", "input_attached", "input_removed", "review_started", "review_finished"].includes(e.type),
  )?.seq;
  useEffect(() => {
    if (lastFilesEvent === undefined) return;
    queryClient.invalidateQueries({ queryKey: ["files", conversationId] });
    queryClient.invalidateQueries({ queryKey: ["checkpoints", conversationId] });
    queryClient.invalidateQueries({ queryKey: ["file-text", conversationId] });
    queryClient.invalidateQueries({ queryKey: ["inputs", conversationId] });
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [lastFilesEvent, conversationId, queryClient]);

  // A title written from the first question, or a rename in another window.
  const lastTitleEvent = events.findLast((e) => e.type === "title_changed")?.seq;
  useEffect(() => {
    if (lastTitleEvent !== undefined) queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [lastTitleEvent, queryClient]);
}

/**
 * Follow new steps only while the person is at the bottom: scrolling up to
 * read something shouldn't be undone by the next step arriving. `bottom` goes
 * on an element at the end of the messages, `onScroll` on the scrolling list.
 */
export function useFollowLatest(conversationId: string, turns: number, events: number, sending: boolean) {
  const bottom = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  // A new conversation, or a new question, brings the view back to the bottom.
  useEffect(() => setFollowing(true), [conversationId, turns]);
  useEffect(() => {
    if (following) bottom.current?.scrollIntoView({ block: "end" });
  }, [events, following, sending]);
  const onScroll = (e: UIEvent<HTMLElement>) => {
    const el = e.currentTarget;
    setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };
  return { bottom, following, onScroll };
}
