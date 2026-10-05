import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, use, useEffect, useMemo, useRef, useState } from "react";

import { api, type Conversation } from "@/api/client";
import { Button, Icon } from "@/components/ui";
import { KnownFilesContext, OpenFileContext } from "@/lib/files";

import { ASSISTANTS, type AssistantId, CompactContext } from "./assistants";
import { useFollowLatest, useRefreshOnEvents } from "./chatHooks";
import { ChatHeader, CompactHeader, ExpressSwitch, RigorSwitch, Title } from "./ChatControls";
import { Composer, type ComposerNote } from "./Composer";
import { useEffortChoice, useExpressEffort } from "./effort";
import { CompactIntro, EmptyState } from "./Intro";
import { RememberButton } from "./KbSuggestionCard";
import type { PendingMessage } from "./Pending";
import { buildTranscript } from "./transcript";
import { PendingTurn, TurnView } from "./TurnView";
import { useConversationEvents } from "./useConversationEvents";

// The chat's parts live beside it: the controls over it (ChatControls, effort),
// what an empty one shows (Intro), each turn (TurnView, Answer), the message
// box (Composer), and what follows the stream (chatHooks). This file keeps the
// conversation's state and sending. Kept importable from here, where the other
// tabs and tests look.
export { ChatHeader, CompactHeader, Title, titleToSend } from "./ChatControls";
export { ComposerBox, type ComposerDraft, type ComposerNote, restored } from "./Composer";
export { useEffortChoice } from "./effort";
export { CompactIntro, EmptyState, safetyLines } from "./Intro";
export { latestRows, PendingTurn, useShownStep } from "./TurnView";

// Events that start or end a turn or its review: DataLab's busy flag changes.
const TURN_EVENTS = new Set(["user_message", "turn_started", "turn_finished", "review_started", "review_finished", "turn_done"]);

/** The shared chat. Every tab that needs an agent uses this component (docked
 *  beside a tab's own content through DockedChat). */
export function Chat({
  conversation,
  headerStart,
  headerActions,
  prepareMessage,
  composerNote,
  autoFocus,
  placeholder,
  sendLabel,
  pending: firstPending,
  active = true,
  assistant,
  tabMode,
  draftKey,
}: {
  conversation: Conversation;
  headerStart?: ReactNode;
  headerActions?: ReactNode;
  /** What's sent for what the person typed (or the starter they picked): DockedChat adds its context here.
   *  It's called when Send is pressed, before anything is sent. */
  prepareMessage?: (text: string) => string;
  /** Above the message box, such as what will be sent along with the message. */
  composerNote?: ComposerNote;
  /** Put the cursor in the message box when the chat opens. */
  autoFocus?: boolean;
  /** The message box's own wording, for a tab that asks for something particular. */
  placeholder?: string;
  sendLabel?: string;
  /** A message already on its way (DockedChat's first one): shown until its event arrives. */
  pending?: PendingMessage;
  /** False while the chat is kept but not shown (a closed docked chat): no stream and no polling until it's back. */
  active?: boolean;
  /** Docked beside this tab: the compact presentation, with this assistant's title. */
  assistant?: AssistantId;
  /** The mode the tab starts its chats in: a conversation of another (an older one) says which it is. */
  tabMode?: string;
  /** Where the message box keeps what's typed (sessionStorage), so hiding the chat doesn't lose it. */
  draftKey?: string;
}) {
  const compact = Boolean(assistant);
  const events = useConversationEvents(conversation.id, active);
  const turns = useMemo(() => buildTranscript(events), [events]);
  const last = turns.at(-1);
  const reviewing = last?.items.some((i) => i.kind === "review" && i.status === "running") ?? false;
  const transcriptRunning = last?.status === "running" || reviewing;
  const [effort, setEffort] = useExpressEffort(conversation, useEffortChoice());
  const queryClient = useQueryClient();
  // A typed message or a starter question (sent as it is, so the agent starts at once).
  const send = useMutation({
    mutationFn: ({ message, kbRequest }: { message: string; kbRequest?: boolean }) =>
      kbRequest ? api.send(conversation.id, message, effort, true) : api.send(conversation.id, message, effort),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  // The message on its way, shown at once with a line under it, until its own
  // event arrives; and what goes back in the message box if it couldn't be sent.
  const [pending, setPending] = useState<PendingMessage | null>(firstPending ?? null);
  const [draft, setDraft] = useState<{ text: string; key: number } | null>(null);
  const arrived = pending !== null && events.some((e) => e.type === "user_message" && e.seq > pending.after);
  useEffect(() => {
    if (arrived) setPending(null);
  }, [arrived]);
  const sending = pending !== null && !arrived;
  // DataLab took it, but its event hasn't come (the stream dropped, say): after
  // a while the chat stops waiting for it, so Send isn't held for good.
  useEffect(() => {
    if (!sending || !send.isSuccess) return;
    const timer = setTimeout(() => setPending(null), STUCK_MS);
    return () => clearTimeout(timer);
  }, [sending, send.isSuccess]);
  // Set at once on a click, before any state update can render: a double click sends once.
  const inFlight = useRef(false);

  // Working: DataLab says so, or the transcript does and DataLab hasn't
  // answered since. A restart mid-turn leaves the transcript without an end,
  // so DataLab's newer word wins.
  const lastTurnEvent = events.findLast((e) => TURN_EVENTS.has(e.type))?.seq;
  const [turnEventAt, setTurnEventAt] = useState(0);
  useEffect(() => {
    if (lastTurnEvent === undefined) return;
    setTurnEventAt(Date.now());
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [lastTurnEvent, queryClient]);
  const status = useQuery({
    queryKey: ["conversations"],
    queryFn: api.conversations,
    // While working, until DataLab answers that it isn't.
    refetchInterval: (query) =>
      active && (conversation.busy || (transcriptRunning && query.state.dataUpdatedAt <= turnEventAt)) ? 3000 : false,
  });
  const running = conversation.busy || (transcriptRunning && status.dataUpdatedAt <= turnEventAt);
  const known = useKnownFiles(conversation.id);

  /**
   * Send what the person typed or picked. It shows at once, and nothing else
   * can be sent until its event arrives (DataLab refuses a second message
   * meanwhile). If it fails, the box gets the text back: the box does that
   * itself for a typed one, `starter` asks for it for a picked one.
   * `kbRequest`: sent with Remember in Knowledge (its popup keeps the text if it fails).
   */
  const sendMessage = (text: string, starter = false, kbRequest = false): Promise<unknown> => {
    if (inFlight.current || running || sending) return Promise.reject(new Error("Already sending."));
    inFlight.current = true;
    setDraft(null);
    setPending({ text, after: events.at(-1)?.seq ?? 0 });
    // Made now, before anything waits: what's sent is what the person saw as they pressed Send.
    const message = prepareMessage ? prepareMessage(text) : text;
    return send
      .mutateAsync({ message, kbRequest })
      .catch((error: unknown) => {
        setPending(null);
        if (starter) setDraft({ text, key: Date.now() });
        throw error;
      })
      .finally(() => {
        inFlight.current = false;
      });
  };

  useRefreshOnEvents(events, running, conversation.id);
  const { bottom, following, onScroll } = useFollowLatest(conversation.id, turns.length, events.length, sending);

  return (
    <CompactContext value={compact}>
      <div className="flex h-full min-h-0 flex-col">
        {assistant ? (
          <CompactHeader
            assistant={assistant}
            kind={conversation.kind}
            title={<Title conversation={conversation} compact />}
            otherMode={tabMode && conversation.mode !== tabMode ? conversation.mode : undefined}
            model={conversation.model}
            effort={effort}
            onEffort={setEffort}
            actions={
              <>
                <ExpressSwitch conversation={conversation} compact />
                {headerActions}
              </>
            }
          />
        ) : (
          <ChatHeader
            start={headerStart}
            title={<Title conversation={conversation} />}
            kind={conversation.kind}
            model={conversation.model}
            effort={effort}
            onEffort={setEffort}
            actions={
              <>
                <RememberButton
                  mode={conversation.mode}
                  onSend={(text) => sendMessage(text, false, true)}
                  busy={running || sending}
                />
                <ExpressSwitch conversation={conversation} />
                {conversation.kind === "data" && <RigorSwitch conversation={conversation} />}
                {headerActions}
              </>
            }
          />
        )}
        <div
          className={clsx("relative min-h-0 flex-1 overflow-y-auto", compact ? "px-4 py-4" : "px-4 py-8 sm:px-8")}
          onScroll={onScroll}
        >
          <div className={clsx("mx-auto flex flex-col", compact ? "gap-8" : "max-w-[48rem] gap-14 2xl:max-w-[54rem]")}>
            {turns.length === 0 && !sending &&
              (assistant ? (
                <CompactIntro
                  assistant={assistant}
                  mode={conversation.mode}
                  kind={conversation.kind}
                  onPick={(text) => void sendMessage(text, true).catch(() => undefined)}
                  starting={send.isPending || running}
                />
              ) : (
                <EmptyState
                  mode={conversation.mode}
                  kind={conversation.kind}
                  onPick={(text) => void sendMessage(text, true).catch(() => undefined)}
                  starting={send.isPending || running}
                />
              ))}
            <KnownFilesContext value={known}>
              {turns.map((turn, index) => (
                <TurnView
                  key={index}
                  turn={turn}
                  conversationId={conversation.id}
                  running={running}
                  last={index === turns.length - 1}
                />
              ))}
            </KnownFilesContext>
            {sending && <PendingTurn text={pending.text} />}
            <div ref={bottom} />
          </div>
          {!following && running && (
            <div className="pointer-events-none sticky bottom-0 flex justify-center">
              <Button
                variant="primary"
                className="dl-in pointer-events-auto"
                onClick={() => bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" })}
              >
                <Icon name="chevron" size={14} className="rotate-90" /> Jump to latest
              </Button>
            </div>
          )}
        </div>
        <Composer
          conversation={conversation}
          running={running}
          // Held from Send until the message's event arrives (then the agent is working).
          sending={send.isPending || sending}
          error={send.error?.message}
          onSend={sendMessage}
          draft={draft}
          note={composerNote}
          autoFocus={autoFocus}
          placeholder={placeholder ?? (assistant && ASSISTANTS[assistant].placeholder)}
          sendLabel={sendLabel}
          compact={compact}
          draftKey={draftKey}
        />
      </div>
    </CompactContext>
  );
}

/**
 * The files this conversation has, by container path, so its answers link
 * only to those. Only where files can be opened (the Workspace); null elsewhere.
 */
function useKnownFiles(conversationId: string): ReadonlySet<string> | null {
  const openFile = use(OpenFileContext);
  const enabled = Boolean(openFile);
  const outputs = useQuery({ queryKey: ["files", conversationId], queryFn: () => api.files(conversationId), enabled });
  const work = useQuery({ queryKey: ["files", conversationId, "work"], queryFn: () => api.files(conversationId, "work"), enabled });
  const results = useQuery({
    queryKey: ["files", conversationId, "results"],
    queryFn: () => api.files(conversationId, "results"),
    enabled,
  });
  return useMemo(() => {
    if (!enabled) return null;
    return new Set([
      ...(outputs.data ?? []).map((f) => `/work/outputs/${f.path}`),
      ...(work.data ?? []).map((f) => `/work/${f.path}`),
      ...(results.data ?? []).map((f) => `/data/oracle/${f.path}`),
    ]);
  }, [enabled, outputs.data, work.data, results.data]);
}

/** After this long, a message DataLab took but whose event never came stops holding Send. */
const STUCK_MS = 20_000;
