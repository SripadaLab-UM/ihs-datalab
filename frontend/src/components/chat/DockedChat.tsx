import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useId, useRef, useState } from "react";

import { api, type Conversation } from "@/api/client";

import { Icon } from "@/components/ui";

import { ASSISTANTS, type AssistantId, CompactContext, draftKeyOf } from "./assistants";
import {
  Chat,
  ChatHeader,
  CompactHeader,
  CompactIntro,
  ComposerBox,
  type ComposerDraft,
  type ComposerNote,
  EmptyState,
  PendingTurn,
  useEffortChoice,
} from "./Chat";
import type { PendingMessage } from "./Pending";

/** Something from the tab beside the chat that goes with each message, such as the SQL being edited. */
export interface ChatContext {
  /** What it is, as the agent will read it: "The query in the SQL editor". */
  label: string;
  /** Its short name on the context row ("sources/fitbit.md"); the label if not given. */
  name?: string;
  text: string;
  /** The code fence's language ("sql", "yaml", "r"...), if it's code. */
  language?: string;
}

/** The message as sent: what the person typed, then the context, fenced so the agent can tell them apart. */
export function withContext(text: string, context?: ChatContext): string {
  if (!context?.text.trim()) return text;
  // A fence longer than any run of backticks in the context, so nothing in it can close the fence.
  const longest = Math.max(0, ...(context.text.match(/`+/g) ?? []).map((run) => run.length));
  const fence = "`".repeat(Math.max(3, longest + 1));
  return `${text}\n\n${context.label}:\n${fence}${context.language ?? ""}\n${context.text.replace(/\n+$/, "")}\n${fence}`;
}

/**
 * The shared chat, docked beside a tab's own content. It shows the conversation
 * `conversationId` or, without one, starts a conversation of `mode` when the
 * first message is sent and reports it through `onConversation` (keep its id to
 * come back to it; remount with a new `key` to start another). Streaming,
 * approvals and Stop are the Workspace's own, through `Chat`.
 *
 * With `context`, the person can choose to send it along with a message: it's
 * off until they tick it, and they can see exactly what will go.
 *
 * With `assistant`, it's the compact presentation of a chat docked beside a
 * tab: the assistant's title, a short intro, and a message box that keeps
 * what's typed (per tab) when the chat is hidden or the tab is left.
 */
export function DockedChat({
  mode,
  conversationId,
  onConversation,
  context,
  model,
  headerStart,
  headerActions,
  placeholder,
  sendLabel,
  onSending,
  assistant,
  draftKey: givenDraftKey,
  handoff,
}: {
  /** The mode a new conversation starts in: "extraction", "engineering"... */
  mode: string;
  conversationId?: string;
  onConversation?: (conversation: Conversation) => void;
  context?: ChatContext;
  /** The model a new conversation uses; DataLab's default if not given. */
  model?: string;
  headerStart?: ReactNode;
  headerActions?: ReactNode;
  /** The message box's own wording ("Describe the data you want", "Generate SQL"). */
  placeholder?: string;
  sendLabel?: string;
  /** Called as Send is pressed (or a starter picked), before anything is sent. */
  onSending?: () => void;
  /** Docked beside this tab: the compact presentation. */
  assistant?: AssistantId;
  /** Where the message box keeps what's typed; by default the assistant's own key. */
  draftKey?: string;
  /** Before a conversation starts: shown in place of the intro and the message box, when
   *  the first message is typed elsewhere on the page (New workflow's description). */
  handoff?: ReactNode;
}) {
  const draftKey = givenDraftKey ?? (assistant ? draftKeyOf(assistant) : undefined);
  const [started, setStarted] = useState<Conversation | null>(null);
  // The first message, shown in the new conversation until its event arrives.
  const [firstMessage, setFirstMessage] = useState<PendingMessage | undefined>(undefined);
  const [includeContext, setIncludeContext] = useState(false);
  // Context about something else now (another file, say): the person ticks
  // it again if they want it sent. Edits to the same thing keep the tick.
  const [contextLabel, setContextLabel] = useState(context?.label);
  if (context?.label !== contextLabel) {
    setContextLabel(context?.label);
    setIncludeContext(false);
  }
  const id = conversationId ?? started?.id;
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations, enabled: Boolean(id) });
  // The list may not have caught up with a conversation just started.
  const conversation = conversations.data?.find((c) => c.id === id) ?? (started?.id === id ? started : undefined);

  const hasContext = Boolean(context?.text.trim());
  // Called as Send is pressed, so the message is what the person saw then.
  const prepare = (text: string) => {
    onSending?.();
    return hasContext && includeContext ? withContext(text, context) : text;
  };
  const note = hasContext
    ? (sending: boolean) => (
        <ContextRow context={context!} included={includeContext} onInclude={setIncludeContext} disabled={sending} />
      )
    : undefined;

  if (id) {
    if (!conversation) {
      return (
        <p className="flex h-full items-center justify-center font-sans text-[13px] text-muted">
          {conversations.isError
            ? "This conversation couldn't be loaded."
            : conversations.isSuccess
              ? "This conversation isn't in DataLab any more."
              : "Opening the conversation…"}
        </p>
      );
    }
    return (
      <Chat
        key={conversation.id}
        conversation={conversation}
        headerStart={headerStart}
        headerActions={headerActions}
        prepareMessage={context || onSending ? prepare : undefined}
        composerNote={note}
        // Just started here: the cursor stays in the message box.
        autoFocus={started?.id === conversation.id || undefined}
        placeholder={placeholder}
        sendLabel={sendLabel}
        pending={started?.id === conversation.id ? firstMessage : undefined}
        assistant={assistant}
        draftKey={draftKey}
      />
    );
  }
  return (
    <NotStarted
      assistant={assistant}
      draftKey={draftKey}
      handoff={handoff}
      mode={mode}
      model={model}
      prepare={prepare}
      note={note}
      headerStart={headerStart}
      headerActions={headerActions}
      placeholder={placeholder}
      sendLabel={sendLabel}
      onStarted={(conversation, text) => {
        setFirstMessage({ text, after: 0 });
        setStarted(conversation);
        onConversation?.(conversation);
      }}
    />
  );
}

/**
 * What the tab has open, as one line over the message box: its name, how long
 * it is, and whether it goes with the message (off until ticked, and it says
 * so). Exactly what would be sent opens under it, only when asked for.
 */
function ContextRow({
  context,
  included,
  onInclude,
  disabled,
}: {
  context: ChatContext;
  included: boolean;
  onInclude: (included: boolean) => void;
  disabled: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const previewId = useId();
  const stateId = useId();
  const lines = context.text.replace(/\n+$/, "").split("\n");
  const name = context.name ?? context.label;
  return (
    <div
      data-testid="context-row"
      className={clsx(
        "mb-1.5 rounded-[3px] border px-2 py-1 font-sans text-[12.5px] transition-colors",
        included ? "border-ink/40 bg-sunken" : "border-line",
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          aria-controls={previewId}
          title={`${expanded ? "Hide" : "Show"} what would be sent: ${context.label}`}
          className="group flex min-w-0 flex-1 items-center gap-1.5 text-left text-ink"
        >
          <Icon name="chevron" size={11} className={clsx("shrink-0 text-faint transition-transform", expanded && "rotate-90")} />
          <Icon name={context.language === "sql" ? "db" : context.language === "markdown" ? "book" : "code"} size={13} className="shrink-0 text-muted" />
          <span className="min-w-0 truncate group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">{name}</span>
          <span className="shrink-0 text-[11.5px] text-faint">
            {lines.length} line{lines.length === 1 ? "" : "s"}
          </span>
        </button>
        <label
          className={clsx(
            "flex shrink-0 items-center gap-1.5",
            disabled ? "cursor-not-allowed" : "cursor-pointer",
            included ? "text-ink" : "text-muted hover:text-ink",
          )}
        >
          <input
            type="checkbox"
            checked={included}
            disabled={disabled}
            onChange={(e) => onInclude(e.target.checked)}
            aria-label={`Send with message: ${context.label}`}
            aria-describedby={stateId}
          />
          {/* A narrow panel keeps room for the name: the box's own label says the rest. */}
          <span className="hidden @[24rem]:inline">Send with message</span>
          <span aria-hidden className="@[24rem]:hidden">Send</span>
        </label>
      </div>
      <p id={stateId} className="pl-[17px] text-[11.5px] text-faint">
        {included ? "Goes with each message you send, as it is then." : "Not sent. Tick Send with message to include it."}
      </p>
      {expanded && (
        <pre
          id={previewId}
          aria-label={`What would be sent: ${context.label}`}
          className={clsx(
            "mt-1 mb-0.5 max-h-48 overflow-auto rounded-[3px] bg-sunken px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed whitespace-pre text-ink",
            !included && "opacity-70",
          )}
        >
          {lines.join("\n")}
        </pre>
      )}
    </div>
  );
}

/** Before the first message: the mode's empty state and a message box that starts the conversation. */
function NotStarted({
  assistant,
  draftKey,
  handoff,
  mode: modeId,
  model,
  prepare,
  note,
  headerStart,
  headerActions,
  placeholder,
  sendLabel,
  onStarted,
}: {
  assistant?: AssistantId;
  draftKey?: string;
  handoff?: ReactNode;
  mode: string;
  model?: string;
  prepare: (text: string) => string;
  note?: ComposerNote;
  headerStart?: ReactNode;
  headerActions?: ReactNode;
  placeholder?: string;
  sendLabel?: string;
  /** With what the person typed or picked (not the context sent along). */
  onStarted: (conversation: Conversation, text: string) => void;
}) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 5 * 60_000 });
  const mode = modes.data?.find((m) => m.id === modeId);
  const [effort, setEffort] = useEffortChoice();
  const queryClient = useQueryClient();
  // The conversation the first try made (or is making): a send that failed is
  // tried again in it, and a second click while it's being made waits for it.
  const creating = useRef<Promise<Conversation> | null>(null);
  // Set at once on Send, before any state update can render: a double click sends once.
  const inFlight = useRef(false);
  // The message on its way, shown at once; and what goes back in the box if it fails.
  const [pending, setPending] = useState<string | null>(null);
  const [draft, setDraft] = useState<ComposerDraft | null>(null);
  const start = useMutation({
    mutationFn: async ({ message }: { message: string; text: string }) => {
      creating.current ??= api.createConversation(modeId, model).then(
        (conversation) => {
          // In the lists straight away, even if the first message then fails.
          queryClient.invalidateQueries({ queryKey: ["conversations"] });
          return conversation;
        },
        (error) => {
          creating.current = null;
          throw error;
        },
      );
      const conversation = await creating.current;
      return (await api.send(conversation.id, message, effort)) ?? conversation;
    },
    onSuccess: (conversation, { text }) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      onStarted(conversation, text);
    },
  });
  // `starter`: a picked question, which the box gets back if it fails (a typed one comes back by itself).
  const send = (text: string, starter = false): Promise<unknown> => {
    if (inFlight.current) return Promise.reject(new Error("Already sending."));
    inFlight.current = true;
    setDraft(null);
    setPending(text);
    // Made now, before anything waits: what's sent is what the person saw as they pressed Send.
    return start
      .mutateAsync({ message: prepare(text), text })
      .catch((error: unknown) => {
        setPending(null);
        if (starter) setDraft({ text, key: Date.now() });
        throw error;
      })
      .finally(() => {
        inFlight.current = false;
      });
  };
  const unknownMode = modes.isSuccess && !mode;
  // Shown once, over the message box, whether a starter or a typed message failed.
  const error = unknownMode ? `DataLab has no “${modeId}” mode.` : start.error?.message;

  if (assistant) {
    const intro = (
      <CompactIntro
        assistant={assistant}
        mode={modeId}
        kind={mode?.kind ?? "data"}
        onPick={(text) => send(text, true).catch(() => undefined)}
        starting={start.isPending}
      />
    );
    return (
      <CompactContext value>
        <div className="flex h-full min-h-0 flex-col">
          <CompactHeader
            assistant={assistant}
            kind={mode?.kind ?? "data"}
            model={model ?? models.data?.default ?? ""}
            effort={effort}
            onEffort={setEffort}
            actions={headerActions}
          />
          <div className="relative min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <div className="flex flex-col gap-8">
              {pending === null && (handoff ?? (mode && intro))}
              {pending !== null && <PendingTurn text={pending} />}
            </div>
          </div>
          {!handoff && (
            <ComposerBox
              running={false}
              sending={pending !== null || start.isPending || !mode}
              error={error}
              onSend={send}
              draft={draft}
              note={note}
              placeholder={placeholder ?? ASSISTANTS[assistant].placeholder}
              sendLabel={sendLabel}
              compact
              draftKey={draftKey}
            />
          )}
          {handoff && error && <p className="px-4 pb-3 font-sans text-[13px] text-danger">{error}</p>}
        </div>
      </CompactContext>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {mode && (
        <ChatHeader
          start={headerStart}
          title={<h1 className="min-w-0 truncate font-serif text-[19px]">New conversation</h1>}
          kind={mode.kind}
          model={model ?? models.data?.default ?? ""}
          effort={effort}
          onEffort={setEffort}
          actions={headerActions}
        />
      )}
      <div className="relative min-h-0 flex-1 overflow-y-auto px-4 py-8 sm:px-8">
        <div className="mx-auto flex max-w-[48rem] flex-col gap-14 2xl:max-w-[54rem]">
          {mode && pending === null && (
            <EmptyState
              mode={mode.id}
              kind={mode.kind}
              onPick={(text) => send(text, true).catch(() => undefined)}
              starting={start.isPending}
            />
          )}
          {pending !== null && <PendingTurn text={pending} />}
        </div>
      </div>
      <ComposerBox
        running={false}
        sending={pending !== null || start.isPending || !mode}
        error={error}
        onSend={send}
        draft={draft}
        note={note}
        placeholder={placeholder}
        sendLabel={sendLabel}
      />
    </div>
  );
}
