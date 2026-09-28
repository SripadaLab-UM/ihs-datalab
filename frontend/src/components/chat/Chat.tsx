import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, use, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, type Conversation, type Effort } from "@/api/client";
import { Button, Chip, FileGlyph, Icon, InfoTip, SessionBadge } from "@/components/ui";
import type { AnyIcon } from "@/components/ui/Icon";
import { KnownFilesContext, OpenFileContext, workspaceFile } from "@/lib/files";

import { ApprovalCard } from "./ApprovalCard";
import { ASSISTANTS, type AssistantId, CompactContext } from "./assistants";
import { Markdown } from "./Markdown";
import { planStatus } from "./plan";
import { ProposalCard } from "./ProposalCard";
import { ShowQueryContext } from "./provenance";
import { activityRows, answerOf, nowLine, type Row } from "./activity";
import { type CheckLine, checkLines, failureChip, failures } from "./checks";
import { type PendingMessage, SendingLine } from "./Pending";
import { GroupRow, HOVER_TITLE, Marker, NowCard, SayRow, StepRow, Story } from "./Story";
import { buildTranscript, canContinue, type Item, type ModelStatus, type Turn } from "./transcript";
import { useConversationEvents } from "./useConversationEvents";

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
  assistant,
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
  /** Docked beside this tab: the compact presentation, with this assistant's title. */
  assistant?: AssistantId;
  /** Where the message box keeps what's typed (sessionStorage), so hiding the chat doesn't lose it. */
  draftKey?: string;
}) {
  const compact = Boolean(assistant);
  const events = useConversationEvents(conversation.id);
  const turns = useMemo(() => buildTranscript(events), [events]);
  const last = turns.at(-1);
  const reviewing = last?.items.some((i) => i.kind === "review" && i.status === "running") ?? false;
  const transcriptRunning = last?.status === "running" || reviewing;
  const bottom = useRef<HTMLDivElement>(null);
  // Follow new steps only while the person is at the bottom: scrolling up to
  // read something shouldn't be undone by the next step arriving.
  const [following, setFollowing] = useState(true);
  const [effort, setEffort] = useEffortChoice();
  const queryClient = useQueryClient();
  // A typed message or a starter question (sent as it is, so the agent starts at once).
  const send = useMutation({
    mutationFn: (message: string) => api.send(conversation.id, message, effort),
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
      conversation.busy || (transcriptRunning && query.state.dataUpdatedAt <= turnEventAt) ? 3000 : false,
  });
  const running = conversation.busy || (transcriptRunning && status.dataUpdatedAt <= turnEventAt);
  const known = useKnownFiles(conversation.id);

  /**
   * Send what the person typed or picked. It shows at once, and nothing else
   * can be sent until its event arrives (DataLab refuses a second message
   * meanwhile). If it fails, the box gets the text back: the box does that
   * itself for a typed one, `starter` asks for it for a picked one.
   */
  const sendMessage = (text: string, starter = false): Promise<unknown> => {
    if (inFlight.current || running || sending) return Promise.reject(new Error("Already sending."));
    inFlight.current = true;
    setDraft(null);
    setPending({ text, after: events.at(-1)?.seq ?? 0 });
    // Made now, before anything waits: what's sent is what the person saw as they pressed Send.
    const message = prepareMessage ? prepareMessage(text) : text;
    return send
      .mutateAsync(message)
      .catch((error: unknown) => {
        setPending(null);
        if (starter) setDraft({ text, key: Date.now() });
        throw error;
      })
      .finally(() => {
        inFlight.current = false;
      });
  };

  // When a turn finishes, refresh what depends on it (busy dots, data accessed).
  useEffect(() => {
    if (!running) {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["data-accessed", conversation.id] });
    }
  }, [running, conversation.id, queryClient]);

  // A new checkpoint or a restore changes the files the side panel shows.
  const lastFilesEvent = events.findLast((e) =>
    ["checkpoint", "files_restored", "input_attached", "input_removed", "review_started", "review_finished"].includes(e.type),
  )?.seq;
  useEffect(() => {
    if (lastFilesEvent === undefined) return;
    queryClient.invalidateQueries({ queryKey: ["files", conversation.id] });
    queryClient.invalidateQueries({ queryKey: ["checkpoints", conversation.id] });
    queryClient.invalidateQueries({ queryKey: ["file-text", conversation.id] });
    queryClient.invalidateQueries({ queryKey: ["inputs", conversation.id] });
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [lastFilesEvent, conversation.id, queryClient]);

  // A title written from the first question, or a rename in another window.
  const lastTitleEvent = events.findLast((e) => e.type === "title_changed")?.seq;
  useEffect(() => {
    if (lastTitleEvent !== undefined) queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [lastTitleEvent, queryClient]);

  // A new conversation, or a new question, brings the view back to the bottom.
  useEffect(() => setFollowing(true), [conversation.id, turns.length]);
  useEffect(() => {
    if (following) bottom.current?.scrollIntoView({ block: "end" });
  }, [events.length, following, sending]);

  return (
    <CompactContext value={compact}>
    <div className="flex h-full min-h-0 flex-col">
      {assistant ? (
        <CompactHeader
          assistant={assistant}
          kind={conversation.kind}
          title={<Title conversation={conversation} compact />}
          model={conversation.model}
          effort={effort}
          onEffort={setEffort}
          actions={headerActions}
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
              {conversation.kind === "data" && <RigorSwitch conversation={conversation} />}
              {headerActions}
            </>
          }
        />
      )}
      <div
        className={clsx("relative min-h-0 flex-1 overflow-y-auto", compact ? "px-4 py-4" : "px-4 py-8 sm:px-8")}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
        }}
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

/** The line over a chat: its title, which session it is, the model and how hard it thinks. */
export function ChatHeader({
  start,
  title,
  kind,
  model,
  effort,
  onEffort,
  actions,
}: {
  start?: ReactNode;
  title: ReactNode;
  kind: "data" | "research";
  model: string;
  effort: Effort;
  onEffort: (effort: Effort) => void;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-3 sm:px-8">
      {start}
      {title}
      <SessionBadge kind={kind} />
      <span className="flex items-center gap-1.5 font-mono text-[11.5px] text-faint">
        {model}
        <span aria-hidden>·</span>
        <EffortSelect effort={effort} onChange={onEffort} />
      </span>
      <div className="ml-auto flex items-center gap-2">{actions}</div>
    </header>
  );
}

/**
 * The line over a docked chat: the tab's assistant, which session it is, and
 * the tab's own actions; under it the conversation (once there is one), the
 * model and how hard it thinks. Two short lines that fit a narrow panel.
 */
export function CompactHeader({
  assistant,
  kind,
  title,
  model,
  effort,
  onEffort,
  actions,
}: {
  assistant: AssistantId;
  kind: "data" | "research";
  /** The conversation's title, once it has started. */
  title?: ReactNode;
  model: string;
  effort: Effort;
  onEffort: (effort: Effort) => void;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-0.5 border-b border-line px-4 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <h2 className="min-w-0 truncate font-sans text-[14px] font-semibold text-ink">{ASSISTANTS[assistant].title}</h2>
        <SessionBadge kind={kind} short />
        <div className="ml-auto flex shrink-0 items-center gap-1">{actions}</div>
      </div>
      <div className="flex min-w-0 items-center gap-2 font-mono text-[11.5px] text-faint">
        {title && <span className="min-w-0 flex-1 truncate">{title}</span>}
        <span className={clsx("flex shrink-0 items-center gap-1.5", !title && "ml-0")}>
          {model}
          <span aria-hidden>·</span>
          <EffortSelect effort={effort} onChange={onEffort} />
        </span>
      </div>
    </header>
  );
}

// What DataLab calls a conversation until its first question names it.
const DEFAULT_TITLE = "New conversation";

/** What an edit that ended with this draft renames the conversation to, if anything.
 *  A draft left as it was when editing began is no rename: the model's title may
 *  have arrived meanwhile, and sending the old one back would wipe it. */
export function titleToSend(draft: string, began: string, current: string): string | null {
  const oneLine = (text: string) => text.split(/\s+/).filter(Boolean).join(" ");
  const title = oneLine(draft);
  if (!title || title === oneLine(began) || title === oneLine(current) || title === DEFAULT_TITLE) return null;
  return title;
}

/** The conversation's title: written from the first question, renamed by clicking it. */
export function Title({ conversation, compact = false }: { conversation: Conversation; compact?: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  // Enter, Escape and blur can all end one edit: only the first one counts.
  const ended = useRef(false);
  // The title as it was when this edit began.
  const began = useRef("");
  const rename = useMutation({
    mutationFn: (title: string) => api.rename(conversation.id, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const finish = (keep: boolean) => {
    if (ended.current) return;
    ended.current = true;
    const title = titleToSend(editing ?? "", began.current, conversation.title);
    setEditing(null);
    if (keep && title) rename.mutate(title);
    requestAnimationFrame(() => button.current?.focus());
  };
  if (editing !== null) {
    return (
      <input
        autoFocus
        aria-label="Conversation title"
        value={editing}
        maxLength={200}
        onChange={(e) => setEditing(e.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") finish(true);
          if (e.key === "Escape") finish(false);
        }}
        className={clsx("min-w-0 border-b border-ink bg-transparent outline-none", compact ? "w-full font-sans text-[13px] text-ink" : "font-serif text-[19px]")}
      />
    );
  }
  const Heading = compact ? "p" : "h1";
  return (
    <Heading className={clsx("min-w-0 truncate", compact ? "font-sans text-[12.5px] text-muted" : "font-serif text-[19px]")}>
      <button
        ref={button}
        type="button"
        onClick={() => {
          ended.current = false;
          began.current = conversation.title;
          setEditing(conversation.title);
        }}
        aria-label={`${conversation.title} (rename)`}
        title="Rename"
        className="max-w-full truncate text-left hover:underline hover:decoration-faint hover:underline-offset-4">
        {conversation.title}
      </button>
    </Heading>
  );
}

function RigorSwitch({ conversation }: { conversation: Conversation }) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => api.setRigorReview(conversation.id, !conversation.rigor_review),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <span className="flex items-center gap-1">
      <label className="flex items-center gap-2 font-sans text-[13px] text-muted hover:text-ink has-[:disabled]:cursor-not-allowed">
        <input
          type="checkbox"
          className="peer sr-only"
          checked={conversation.rigor_review}
          onChange={() => toggle.mutate()}
          disabled={toggle.isPending}
        />
        <span
          aria-hidden="true"
          className="relative h-4 w-7 rounded-full bg-line transition-colors peer-checked:bg-ink peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink peer-disabled:opacity-70 after:absolute after:top-0.5 after:left-0.5 after:size-3 after:rounded-full after:bg-surface after:transition-transform peer-checked:after:translate-x-3"
        />
        Rigor review
      </label>
      <InfoTip term="rigor-review" align="end" />
    </span>
  );
}

/** What a conversation with no questions yet shows: what its mode is for, and starters to pick. */
export function EmptyState({
  mode: modeId,
  kind,
  onPick,
  starting,
  error,
}: {
  mode: string;
  kind: "data" | "research";
  onPick: (text: string) => void;
  starting: boolean;
  error?: string;
}) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const mode = modes.data?.find((m) => m.id === modeId);
  const data = kind === "data";
  return (
    <div className="mt-6 flex flex-col gap-10">
      {/* The Workspace's own: a docked chat has CompactIntro instead. */}
      <div>
        <p className="dl-label">{mode?.label ?? "New conversation"}</p>
        <h2 className="mt-3 font-serif text-[42px] leading-[1.1] tracking-[-0.01em] text-balance">{mode?.question ?? "What would you like to find out?"}</h2>
        <p className="mt-4 max-w-[52ch] font-serif text-[18px] leading-relaxed text-muted">{mode?.description}</p>
      </div>
      <ul className="grid gap-x-6 gap-y-3 border-t border-line pt-4 font-sans text-[13px] text-muted sm:grid-cols-3">
        {safetyLines(kind, mode?.queries).map(([icon, text]) => (
          <li key={text} className="flex items-start gap-2">
            <Icon name={icon as "db"} size={14} className={clsx("mt-0.5 shrink-0", data ? "text-data" : "text-research")} />
            {text}
          </li>
        ))}
      </ul>
      {mode && mode.starters.length > 0 && (
        <div>
          <p className="dl-label mb-2">Try one of these, or ask your own</p>
          {error && <p className="mb-2 font-sans text-[13px] text-danger">{error}</p>}
          <ul className="border-t border-line">
            {mode.starters.map((starter) => (
              <li key={starter} className="border-b border-line">
                <button
                  onClick={() => onPick(starter)}
                  disabled={starting}
                  className="group flex w-full items-baseline gap-4 py-3.5 text-left font-serif text-[18.5px] leading-snug text-ink"
                >
                  <span className="flex-1 group-enabled:group-hover:underline group-enabled:group-hover:decoration-faint group-enabled:group-hover:underline-offset-4">{starter}</span>
                  <Icon name="chevron" size={14} className="shrink-0 text-faint group-enabled:group-hover:text-ink" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** What a session may and may not do, said wherever a conversation starts. */
export function safetyLines(kind: "data" | "research", queries: boolean | undefined): [AnyIcon, string][] {
  return kind === "data"
    ? [
        // Knowledge writing has the catalog tools only.
        queries === false
          ? ["db", "Reads the database catalog (tables and columns), never rows"]
          : ["db", "Queries the IHS database, read-only"],
        ["eye", "Shows you everything it reads and runs"],
        ["lock", "Websites blocked; the model is U-M's approved GPT service"],
      ]
    : [
        ["globe", "Searches the web and reads papers"],
        ["eye", "Shows you everything it reads"],
        ["lock", "No connection to the study database"],
      ];
}

/**
 * A docked chat before its first message: one sentence on what this tab's
 * assistant is for, one or two starters, and what it can do folded into a
 * single line to open.
 */
export function CompactIntro({
  assistant,
  mode: modeId,
  kind,
  onPick,
  starting,
  error,
}: {
  assistant: AssistantId;
  mode: string;
  kind: "data" | "research";
  onPick: (text: string) => void;
  starting: boolean;
  error?: string;
}) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const mode = modes.data?.find((m) => m.id === modeId);
  const copy = ASSISTANTS[assistant];
  const [open, setOpen] = useState(false);
  const listId = useId();
  const lines = [...copy.can, ...safetyLines(kind, mode?.queries)];
  return (
    <div data-testid="compact-intro" className="flex flex-col gap-4">
      <p className="font-serif text-[15.5px] leading-snug text-muted">{copy.description}</p>
      {mode && mode.starters.length > 0 && (
        <div>
          <p className="dl-label mb-1">Try</p>
          {error && <p className="mb-1 font-sans text-[12.5px] text-danger">{error}</p>}
          <ul className="border-t border-line">
            {mode.starters.slice(0, 2).map((starter) => (
              <li key={starter} className="border-b border-line">
                <button
                  onClick={() => onPick(starter)}
                  disabled={starting}
                  className="group flex w-full items-baseline gap-3 py-2 text-left font-serif text-[15px] leading-snug text-ink"
                >
                  <span className="flex-1 group-enabled:group-hover:underline group-enabled:group-hover:decoration-faint group-enabled:group-hover:underline-offset-4">{starter}</span>
                  <Icon name="chevron" size={12} className="shrink-0 text-faint group-enabled:group-hover:text-ink" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="font-sans text-[12.5px]">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls={listId}
          className="group flex items-center gap-1.5 text-muted hover:text-ink"
        >
          <Icon name="chevron" size={11} className={clsx("transition-transform", open && "rotate-90")} />
          <span className={HOVER_TITLE}>What it can do</span>
        </button>
        {open && (
          <ul id={listId} className="mt-2 flex flex-col gap-1.5 text-muted">
            {lines.map(([icon, text]) => (
              <li key={text} className="flex items-start gap-2">
                <Icon name={icon} size={13} className={clsx("mt-0.5 shrink-0", kind === "data" ? "text-data" : "text-research")} />
                {text}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function TurnView({
  turn,
  conversationId,
  running,
  last,
}: {
  turn: Turn;
  conversationId: string;
  running: boolean;
  last: boolean;
}) {
  const answer = answerOf(turn);
  const live = turn.status === "running" && running;
  const rows = activityRows(turn.items, live);
  const storyRows = rows.filter((row) => row.type !== "review" && row.type !== "proposal");
  // Proposed knowledge edits wait for the person: never folded away with the story.
  const proposals = rows.flatMap((row) => (row.type === "proposal" ? [row.proposal] : []));
  const reviews = turn.items.filter((item): item is Extract<Item, { kind: "review" }> => item.kind === "review");
  const reasoning = [...turn.items].reverse().find((item) => item.kind === "reasoning");
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stop(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const compact = use(CompactContext);
  // Docked, a long story in progress shows its latest steps; the earlier ones fold into one line.
  const [allSteps, setAllSteps] = useState(false);
  const { shown: shownRows, folded } = compact && !allSteps ? latestRows(storyRows) : { shown: storyRows, folded: 0 };
  const renderRow = (row: Row) => {
    switch (row.type) {
      case "step":
        return <StepRow step={row.step} />;
      case "group":
        return <GroupRow row={row} />;
      case "say":
        return <SayRow text={row.text} />;
      case "approval":
        return (
          <div className="py-2">
            <ApprovalCard conversationId={conversationId} approval={row.approval} />
          </div>
        );
      case "notice":
        return (
          <p className={clsx("flex items-baseline gap-3 py-1 font-sans text-[13.5px]", row.tone === "error" ? "text-danger" : "text-muted")}>
            <Marker tone={row.tone === "error" ? "error" : "done"} open={null} />
            {row.text}
          </p>
        );
      default:
        return null;
    }
  };
  const story = <Story rows={shownRows} renderRow={renderRow} />;
  // Completed with an answer: the answer leads, the work behind it folds away.
  // A failed or stopped turn keeps its whole story in view.
  const finished = Boolean(answer) && turn.status === "completed" && !live;
  return (
    <article className={clsx("flex flex-col", compact ? "gap-3" : "gap-5")} data-question-seq={turn.seq}>
      {turn.userText && <Question text={turn.userText} continues={turn.continues} />}
      {!finished && folded > 0 && (
        <button
          type="button"
          onClick={() => setAllSteps(true)}
          className="group flex items-start gap-3 text-left font-sans text-[13px] text-muted"
        >
          <Marker tone="done" open={false} />
          <span className={HOVER_TITLE}>
            {folded} earlier step{folded === 1 ? "" : "s"}
          </span>
        </button>
      )}
      {!finished && story}
      {live && !answer && (
        <NowCard
          line={
            turn.model?.state === "retrying"
              ? retryLine(turn.model)
              : // Before turn_started, DataLab is starting (or checking) the turn's sandbox.
                !turn.started && rows.length === 0
                ? "Starting the agent's sandbox…"
                : nowLine(rows, reasoning?.kind === "reasoning" ? reasoning.text : "")
          }
          waiting={waitingFor(rows)}
          onStop={() => stop.mutate()}
          stopping={stop.isPending}
        />
      )}
      {answer && (
        <AnswerCard
          answer={answer}
          trace={turn.trace}
          streaming={live}
          turn={turn}
          checks={checkLines({
            trace: turn.trace,
            review: reviews.at(-1),
            reviewing: reviews.at(-1)?.status === "running" && running,
            failed: live ? [] : failures(storyRows),
          })}
        />
      )}
      {/* Only the latest review: one run again replaces one that didn't finish. */}
      {reviews.slice(-1).map((review) => (
        <ReviewBox key={`review-${reviews.length}`} review={review} conversationId={conversationId} running={running} last={last} />
      ))}
      {proposals.map((proposal) => (
        <ProposalCard key={proposal.id} proposal={proposal} />
      ))}
      {finished && <MadeHere items={turn.items} conversationId={conversationId} />}
      {finished && (
        <HowItWasMade rows={storyRows}>
          <Story rows={storyRows} renderRow={renderRow} />
        </HowItWasMade>
      )}
      {last && !running && canContinue(turn) && <ContinueButton conversationId={conversationId} />}
      {turn.status === "interrupted" && (
        <p className="font-serif text-[16px] text-muted italic">Stopped. Anything it saved is in History.</p>
      )}
      {turn.status === "failed" && (
        <p className="font-sans text-[14px] text-danger">
          This turn ended with an error (shown above). Anything it saved is in History.
        </p>
      )}
    </article>
  );
}

/** The output files this turn changed that still exist, to open straight from the answer. */
function MadeHere({ items, conversationId }: { items: Item[]; conversationId: string }) {
  const openFile = use(OpenFileContext);
  const outputs = useQuery({ queryKey: ["files", conversationId], queryFn: () => api.files(conversationId) });
  const existing = new Set(outputs.data?.map((file) => `/work/outputs/${file.path}`));
  const paths = [
    ...new Set(items.flatMap((item) => (item.kind === "files" ? item.paths : [])).filter((p) => existing.has(p))),
  ];
  if (paths.length === 0 || !openFile) return null;
  return (
    <section>
      <h3 className="dl-label mb-2">Made in this turn</h3>
      <ul className="flex flex-wrap gap-2">
        {paths.map((path) => {
          const file = workspaceFile(path);
          if (!file) return null;
          return (
            <li key={path}>
              <button
                type="button"
                onClick={() => openFile(file)}
                title={path}
                className="inline-flex max-w-[22rem] items-center gap-2 rounded-[3px] border border-line bg-surface px-2.5 py-1.5 font-sans text-[13px] hover:border-ink"
              >
                <FileGlyph kind={file.kind} size={20} />
                <span className="truncate">{path.replace(/^\/work\/outputs\//, "")}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * The work behind a finished answer, folded into one row that says what it
 * amounted to. Failed steps and the plan are named on the row itself, so
 * folding never hides them.
 */
function HowItWasMade({ rows, children }: { rows: Row[]; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  if (rows.length === 0) return null;
  const steps = rows.flatMap((row) => (row.type === "step" ? [row.step] : row.type === "group" ? row.steps : []));
  const count = (icon: string) => steps.filter((step) => step.icon === icon).length;
  const files = new Set(
    steps.flatMap((step) => (step.detail?.kind === "files" ? step.detail.paths : [])),
  ).size;
  // Failed steps and error notices are named on the row, so folding never hides them,
  // with whether a later step of the same kind worked.
  const failedChip = failureChip(failures(rows));
  const facts = [
    steps.length > 0 && `${steps.length} step${steps.length === 1 ? "" : "s"}`,
    count("db") && `${count("db")} quer${count("db") === 1 ? "y" : "ies"}`,
    count("book") && `${count("book")} lab guide${count("book") === 1 ? "" : "s"} read`,
    files && `${files} file${files === 1 ? "" : "s"} changed`,
  ].filter(Boolean) as string[];
  const planChip = planStatus(rows.flatMap((row) => (row.type === "approval" ? [row.approval] : [])));
  return (
    <section className="border-y border-line">
      <button type="button" data-tour="how-made" onClick={() => setOpen(!open)} aria-expanded={open} className="group flex w-full items-start gap-3 py-3 text-left">
        <Marker tone="done" open={open} />
        <span className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="font-sans text-[14.5px] text-ink">
            <span className={HOVER_TITLE}>How this answer was made</span>
          </span>
          <span className="flex flex-wrap gap-1.5">
            {facts.map((fact) => (
              <Chip key={fact}>{fact}</Chip>
            ))}
            {planChip && <Chip tone={planChip.tone}>{planChip.text}</Chip>}
            {failedChip && <Chip tone={failedChip.tone}>{failedChip.text}</Chip>}
          </span>
        </span>
      </button>
      {open && <div className="pb-4 pl-[19px]">{children}</div>}
    </section>
  );
}

// A docked chat's story in progress: this many of its latest rows show.
const LATEST_ROWS = 4;

/**
 * The rows a docked chat shows of a story in progress: the latest few, and
 * every approval waiting for the person and every error, wherever they are.
 */
export function latestRows(rows: Row[]): { shown: Row[]; folded: number } {
  if (rows.length <= LATEST_ROWS + 1) return { shown: rows, folded: 0 };
  const cut = rows.length - LATEST_ROWS;
  const kept = (row: Row, i: number) =>
    i >= cut ||
    row.type === "approval" ||
    (row.type === "notice" && row.tone === "error") ||
    (row.type === "step" && row.step.tone === "error");
  const shown = rows.filter(kept);
  return { shown, folded: rows.length - shown.length };
}

/** The first line of Markdown text, without its markup. */
function firstLine(text: string): string {
  const line = text.split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  return line.replace(/^#+\s*|^[-*]\s+|^\d+\.\s+/, "").replace(/\*\*|`/g, "");
}

/** The person's question, set large; a long, pasted one reads as text, not as a heading. */
function Question({ text, continues }: { text: string; continues?: boolean }) {
  const compact = use(CompactContext);
  if (continues) return <p className="dl-label">Continued after an interruption</p>;
  if (compact) {
    // Docked: the question as a message, not a heading.
    return (
      <p className={clsx("font-serif leading-snug break-words whitespace-pre-wrap text-ink", text.length > 220 ? "text-[15px]" : "text-[17px]")}>
        {text}
      </p>
    );
  }
  if (text.length > 220) {
    return <p className="font-serif text-[19px] leading-relaxed break-words whitespace-pre-wrap text-ink">{text}</p>;
  }
  return (
    <h2 className="font-serif text-[28px] leading-[1.18] tracking-[-0.005em] break-words whitespace-pre-wrap text-balance text-ink">
      {text}
    </h2>
  );
}

/** A message on its way: the question as it will be, with what's happening under it. */
export function PendingTurn({ text }: { text: string }) {
  return (
    <article className="flex flex-col gap-5" data-testid="pending-message">
      <Question text={text} />
      <SendingLine />
    </article>
  );
}

/** The live line while DataLab waits to retry a model request (relay/recovery.py). */
function retryLine(model: ModelStatus): string {
  const when = model.waitSeconds ? ` in about ${model.waitSeconds} s` : "";
  return model.kind === "connection"
    ? `Couldn't reach U-M GPT. DataLab will try again${when}.`
    : `The model service is busy. DataLab will try again${when}.`;
}

/** Picks up a turn that stopped part-way, in the same thread: nothing is sent twice. */
function ContinueButton({ conversationId }: { conversationId: string }) {
  const queryClient = useQueryClient();
  const go = useMutation({
    mutationFn: () =>
      api.continueTurn(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <div className="flex items-center gap-3">
      <Button variant="primary" onClick={() => go.mutate()} disabled={go.isPending || go.isSuccess}>
        Continue
      </Button>
      {go.error && <span className="font-sans text-[13px] text-danger">{go.error.message}</span>}
    </div>
  );
}

/** What the agent is waiting for the person to decide, if anything. */
function waitingFor(rows: ReturnType<typeof activityRows>): string | undefined {
  const pending = rows.find((row) => row.type === "approval" && row.approval.state === "pending");
  if (pending?.type !== "approval") return undefined;
  return pending.approval.approvalKind === "analysis_plan"
    ? "Review the analysis plan above, then approve it or say what to change."
    : "Check the question for the research helper above, then send it or not.";
}

/** The answer, set apart from the work behind it. */
function AnswerCard({
  answer,
  trace,
  streaming,
  turn,
  checks,
}: {
  answer: string;
  trace: Turn["trace"];
  streaming: boolean;
  turn: Turn;
  checks: CheckLine[];
}) {
  const openFile = use(OpenFileContext);
  const openQuery = use(ShowQueryContext);
  const compact = use(CompactContext);
  // Where each number appears, once the turn's provenance has arrived.
  const numbers = useMemo(() => {
    if (!turn.provenance || streaming) return undefined;
    const commands = new Map(turn.items.flatMap((i) => (i.kind === "command" ? [[i.id, i.command] as const] : [])));
    return {
      sources: new Map(turn.provenance.numbers.map((n) => [n.text, n.sources])),
      links: {
        commandText: (id: string) => commands.get(id),
        // Only where there's a Queries tab to show it in; elsewhere, just its id.
        openQuery: openQuery ?? undefined,
        openFile: openFile
          ? (path: string) => {
              const file = workspaceFile(`/work/${path}`);
              if (file) openFile(file);
            }
          : undefined,
      },
    };
  }, [turn, streaming, openFile, openQuery]);
  return (
    <section
      data-tour="answer"
      className={clsx(
        "rounded-[4px] border border-line border-t-2 border-t-ink bg-surface",
        compact ? "min-w-0 px-4 pt-3 pb-4 [&_.prose-datalab]:text-[1rem]" : "mt-2 px-6 pt-4 pb-5 [&_.prose-datalab]:text-[1.2rem]",
      )}
    >
      <h3 className="mb-3 flex items-center gap-2 font-sans text-[12px] font-semibold tracking-[0.08em] text-ink uppercase">
        {streaming ? "Writing the answer…" : "Answer"}
      </h3>
      <Markdown text={answer} numbers={numbers} answer />
      {numbers && turn.provenance && turn.provenance.more_numbers > 0 && (
        <p className="mt-3 font-sans text-[12.5px] text-muted">
          {turn.provenance.more_numbers} more number{turn.provenance.more_numbers === 1 ? "" : "s"} in this answer
          weren't checked for where {turn.provenance.more_numbers === 1 ? "it appears" : "they appear"}.
        </p>
      )}
      {!streaming && checks.length > 0 && <Checks lines={checks} untraced={trace?.untraced ?? []} folded={compact} />}
    </section>
  );
}

/**
 * The checks on the answer, each saying whose check it is and whether a
 * problem remains: DataLab's own number check first, then the agent's rigor
 * review, why they differ when they seem to, and the steps that failed.
 */
function Checks({ lines, untraced, folded = false }: { lines: CheckLine[]; untraced: string[]; folded?: boolean }) {
  // Docked, the checks fold into their heading, which says whether any needs a look.
  const [open, setOpen] = useState(!folded);
  const listId = useId();
  const worst = lines.some((l) => l.tone === "bad") ? "bad" : lines.some((l) => l.tone === "attn") ? "attn" : null;
  return (
    <div data-testid="answer-checks" className={clsx("flex flex-col gap-1.5 border-t border-line font-sans text-[13px] leading-snug", folded ? "mt-4 pt-2" : "mt-6 pt-3")}>
      <h4 className="dl-label flex items-center gap-1.5">
        {folded ? (
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls={listId} className="group flex items-center gap-1.5 uppercase">
            <Icon name="chevron" size={11} className={clsx("transition-transform", open && "rotate-90")} />
            <span className={HOVER_TITLE}>Checks on this answer</span>
            {!open && worst && <Chip tone={worst}>{worst === "bad" ? "a problem" : "needs a look"}</Chip>}
          </button>
        ) : (
          "Checks on this answer"
        )}{" "}
        <InfoTip term="trace-and-provenance" />
      </h4>
      {open && (
      <ul id={listId} className="flex flex-col gap-1.5">
        {lines.map((line) => (
          <li
            key={line.key}
            className={clsx(
              "flex items-baseline gap-2",
              line.key === "differ" ? "pl-[18px] text-muted italic" : "text-ink",
            )}
          >
            {line.key !== "differ" &&
              (line.tone === "plain" ? (
                <span aria-hidden className="mx-[3.5px] inline-block h-[5px] w-[5px] shrink-0 -translate-y-[1px] rounded-full bg-muted" />
              ) : (
                <Icon
                  name={line.tone === "good" ? "check" : "alert"}
                  size={12}
                  className={clsx(
                    "shrink-0 translate-y-[1px]",
                    line.tone === "good" && "text-data",
                    line.tone === "attn" && "text-attn",
                    line.tone === "bad" && "text-danger",
                  )}
                />
              ))}
            <span className="min-w-0">
              {line.text}
              {line.key === "trace" && untraced.length > 0 && (
                <span className="mt-1 flex flex-wrap gap-1.5">
                  {untraced.slice(0, 8).map((n) => (
                    <Chip key={n} tone="attn" title="Not in this turn's query results, command output, or data files: check it">
                      {n}
                    </Chip>
                  ))}
                  {untraced.length > 8 && <Chip tone="attn">+{untraced.length - 8}</Chip>}
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
      )}
    </div>
  );
}

function ReviewBox({
  review,
  conversationId,
  running,
  last,
}: {
  review: Extract<Item, { kind: "review" }>;
  conversationId: string;
  running: boolean;
  last: boolean;
}) {
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stop(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const address = useMutation({
    mutationFn: () =>
      api.send(conversationId, "Please address the problems the rigor review found, where you can, and say which you couldn't."),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const again = useMutation({
    mutationFn: () => api.rerunReview(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  // Collapsed once done: its header says what it is; the checklist is one click away.
  const [open, setOpen] = useState(false);
  const reviewing = review.status === "running" && running;
  const unfinished = !reviewing && (review.status === "failed" || review.status === "stopped");
  return (
    <section className="border-y border-line">
      <div className="flex items-start gap-3 py-3">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="group flex flex-1 items-start gap-3 text-left">
        {reviewing ? <Marker tone="now" open={null} /> : <Marker tone="done" open={open} />}
        <span className="flex-1">
          <span className="block font-sans text-[14.5px] text-ink">
            <span className={HOVER_TITLE}>Rigor review</span>
          </span>
          <span className="block font-serif text-[15.5px] text-muted italic">
            {reviewing
              ? "The agent is checking its own work against the lab's checklist…"
              : review.status === "done"
                ? "The agent's check of its own work: a second opinion, not proof."
                : review.status === "stopped"
                  ? "Stopped before it finished."
                  : "Didn't finish (the model service may have been busy). The answer above is unaffected."}
          </span>
          {!open && review.status === "done" && review.text && (
            // Its opening line, as written: the review's own words, not a verdict made from them.
            <span className="mt-1 line-clamp-1 font-sans text-[13px] text-ink">{firstLine(review.text)}</span>
          )}
        </span>
      </button>
        {reviewing && (
          <button
            type="button"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 enabled:hover:text-danger"
          >
            Stop the review
          </button>
        )}
        {unfinished && last && (
          <button
            type="button"
            onClick={() => again.mutate()}
            disabled={running || again.isPending || again.isSuccess}
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 enabled:hover:decoration-ink"
          >
            Run the review again
          </button>
        )}
      </div>
      {again.error && <p className="pb-3 font-sans text-[13px] text-danger">{again.error.message}</p>}
      {open && review.text && review.status === "done" && (
        <div className="pb-5 pl-[19px]">
          <Markdown text={review.text} />
          <div className="mt-3 flex items-center justify-end gap-3">
            {address.error && <p className="text-[13px] text-danger">{address.error.message}</p>}
            <Button
              onClick={() => address.mutate()}
              disabled={running || address.isPending || address.isSuccess}
              title={running ? "Wait until the agent has finished" : undefined}
            >
              Ask the agent to address these
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function Composer({
  conversation,
  running,
  sending,
  error,
  onSend,
  draft,
  note,
  autoFocus,
  placeholder,
  sendLabel,
  compact,
  draftKey,
}: {
  conversation: Conversation;
  running: boolean;
  sending: boolean;
  error?: string;
  onSend: (text: string) => Promise<unknown>;
  draft?: ComposerDraft | null;
  note?: ComposerNote;
  autoFocus?: boolean;
  placeholder?: string;
  sendLabel?: string;
  compact?: boolean;
  draftKey?: string;
}) {
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stop(conversation.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <ComposerBox
      running={running}
      sending={sending}
      error={error}
      onSend={onSend}
      draft={draft}
      onStop={() => stop.mutate()}
      stopping={stop.isPending}
      note={note}
      autoFocus={autoFocus}
      placeholder={placeholder}
      sendLabel={sendLabel}
      compact={compact}
      draftKey={draftKey}
    />
  );
}

/** A message that couldn't be sent, back in the box before whatever was typed since. */
export function restored(message: string, typedSince: string): string {
  if (!typedSince.trim()) return message;
  return /\n$/.test(message) ? message + typedSince : `${message}\n${typedSince}`;
}

/** After this long, a message DataLab took but whose event never came stops holding Send. */
const STUCK_MS = 20_000;

/** What sits over the message box; given whether a message is being sent, to hold still meanwhile. */
export type ComposerNote = ReactNode | ((sending: boolean) => ReactNode);

/** Text to put back in the message box (a message that couldn't be sent); a new `key` puts it back again. */
export type ComposerDraft = { text: string; key: number };

// The box grows with what's typed (wrapped lines too), up to about eight lines;
// docked, about six, and then it scrolls.
const BOX_MAX = 220;
const COMPACT_BOX_MAX = 150;

/** What's typed in a message box, kept under `key` for this browser tab (sessionStorage) when there is one. */
function useDraftText(key: string | undefined): [string, (next: string | ((current: string) => string)) => void] {
  const [text, setText] = useState(() => {
    if (!key) return "";
    try {
      return sessionStorage.getItem(key) ?? "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    if (!key) return;
    try {
      if (text) sessionStorage.setItem(key, text);
      else sessionStorage.removeItem(key);
    } catch {
      // Storage can be unavailable (a private window): the draft lasts while the box is open.
    }
  }, [key, text]);
  return [text, setText];
}

/** The message box under a chat. It clears as Send is pressed, and gets the text back if `onSend` fails. */
export function ComposerBox({
  running,
  sending,
  error,
  onSend,
  draft,
  onStop,
  stopping = false,
  note,
  autoFocus,
  placeholder = "Ask a question, or say what to do next",
  sendLabel = "Send",
  compact = false,
  draftKey,
}: {
  running: boolean;
  sending: boolean;
  error?: string;
  onSend: (text: string) => Promise<unknown>;
  draft?: ComposerDraft | null;
  onStop?: () => void;
  stopping?: boolean;
  note?: ComposerNote;
  autoFocus?: boolean;
  placeholder?: string;
  sendLabel?: string;
  /** Docked beside a tab: a smaller box whose button shrinks to its icon in a narrow panel. */
  compact?: boolean;
  /** Keep what's typed under this key (sessionStorage), so closing or reopening the chat doesn't lose it. */
  draftKey?: string;
}) {
  const [text, setText] = useDraftText(draftKey);
  // A message that couldn't be sent comes back, before anything typed meanwhile.
  useEffect(() => {
    if (draft) setText((current) => restored(draft.text, current));
  }, [draft, setText]);
  const box = useRef<HTMLTextAreaElement>(null);
  const max = compact ? COMPACT_BOX_MAX : BOX_MAX;
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, max)}px`;
    // Past its most, it scrolls within itself: the messages above keep their room.
    element.style.overflowY = element.scrollHeight > max ? "auto" : "hidden";
  }, [text, max]);

  const submit = () => {
    const typed = text;
    const message = typed.trim();
    if (!message || running || sending) return;
    // Cleared at once: the message shows in the chat. If it fails, it comes back
    // here as it was typed, before anything typed since, and the error shows above.
    setText("");
    onSend(message).catch(() => setText((current) => restored(typed, current)));
  };

  // Docked, the button's label shows only where the panel has room for it and the box.
  const label = (words: string) => (compact ? <span className="hidden @[20rem]:inline">{words}</span> : words);
  return (
    <footer data-tour="composer" className={clsx("shrink-0", compact ? "@container px-3 pt-1.5 pb-3" : "px-4 pt-2 pb-6 sm:px-8")}>
      <div className={clsx(!compact && "mx-auto max-w-[48rem] 2xl:max-w-[54rem]")}>
        {error && <p className="mb-2 text-sm text-danger">{error}</p>}
        {typeof note === "function" ? note(sending) : note}
        {/* The hint sits under the box, so the box itself asks a plain question. */}
        <div
          data-testid="composer-box"
          className={clsx(
            "flex min-w-0 items-end gap-2 rounded-[4px] border border-edge bg-field transition-colors focus-within:border-ink focus-within:shadow-[0_0_0_1px_var(--color-ink)]",
            compact ? "p-1.5 pl-2.5" : "p-2 pl-3",
          )}
        >
          <textarea
            ref={box}
            autoFocus={autoFocus}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            aria-label="Your question or instruction"
            placeholder={
              running
                ? compact
                  ? "Working… Stop it, or wait"
                  : "The agent is working. You can stop it, or wait to ask more."
                : placeholder
            }
            className={clsx(
              "min-w-0 flex-1 resize-none bg-transparent font-sans leading-relaxed outline-none placeholder:text-faint",
              compact ? "min-h-8 py-1 text-[14px] placeholder:truncate" : "min-h-10 py-1.5 text-[15px]",
            )}
          />
          {running ? (
            <Button
              variant="secondary"
              onClick={onStop}
              disabled={stopping || !onStop}
              aria-label={compact ? "Stop" : undefined}
              className={clsx(compact && "shrink-0 px-2 @[20rem]:px-3")}
            >
              <Icon name="stop" size={14} /> {label("Stop")}
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={submit}
              disabled={!text.trim() || sending}
              aria-label={compact ? sendLabel : undefined}
              title={compact ? sendLabel : undefined}
              className={clsx(compact && "shrink-0 px-2 @[20rem]:px-3")}
            >
              <Icon name="send" size={14} /> {label(sendLabel)}
            </Button>
          )}
        </div>
        <p className="mt-1 px-1 font-sans text-[11.5px] text-faint">
          {compact
            ? "Enter to send · Shift+Enter for a new line"
            : "Enter to send · Shift+Enter for a new line · the agent shows its steps as it works"}
        </p>
      </div>
    </footer>
  );
}

const EFFORT_KEY = "datalab.effort";

/** How hard the agent thinks, remembered in this browser as the default for new messages. */
export function useEffortChoice(): [Effort, (effort: Effort) => void] {
  const [effort, setEffort] = useState<Effort>(() => {
    try {
      const saved = localStorage.getItem(EFFORT_KEY);
      return saved === "low" || saved === "medium" || saved === "high" ? saved : "medium";
    } catch {
      return "medium";
    }
  });
  const choose = (next: Effort) => {
    setEffort(next);
    try {
      localStorage.setItem(EFFORT_KEY, next);
    } catch {
      // Private windows may refuse storage: the choice still holds for this page.
    }
  };
  return [effort, choose];
}

function EffortSelect({ effort, onChange }: { effort: Effort; onChange: (effort: Effort) => void }) {
  return (
    <select
      value={effort}
      onChange={(e) => onChange(e.target.value as Effort)}
      aria-label="How hard the agent thinks"
      title="How hard the agent thinks, for the next message you send"
      className="bg-transparent font-sans text-[12px] text-muted enabled:hover:text-ink"
    >
      <option value="low">Quick</option>
      <option value="medium">Balanced</option>
      <option value="high">Thorough</option>
    </select>
  );
}
