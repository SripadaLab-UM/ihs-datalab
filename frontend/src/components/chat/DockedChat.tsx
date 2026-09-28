import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useId, useRef, useState } from "react";

import { api, type Conversation } from "@/api/client";

import { Chat, ChatHeader, ComposerBox, type ComposerNote, EmptyState, useEffortChoice } from "./Chat";

/** Something from the tab beside the chat that goes with each message, such as the SQL being edited. */
export interface ChatContext {
  /** What it is, as the agent will read it: "The query in the SQL editor". */
  label: string;
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
}) {
  const [started, setStarted] = useState<Conversation | null>(null);
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
        <ContextNote context={context!} included={includeContext} onInclude={setIncludeContext} disabled={sending} />
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
      />
    );
  }
  return (
    <NotStarted
      mode={mode}
      model={model}
      prepare={prepare}
      note={note}
      headerStart={headerStart}
      headerActions={headerActions}
      placeholder={placeholder}
      sendLabel={sendLabel}
      onStarted={(conversation) => {
        setStarted(conversation);
        onConversation?.(conversation);
      }}
    />
  );
}

const PREVIEW_LINES = 3;

/** The choice to send the context, and exactly what would be sent. */
function ContextNote({
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
  const lines = context.text.replace(/\n+$/, "").split("\n");
  const long = lines.length > PREVIEW_LINES;
  return (
    <div className="mb-2 px-1 font-sans text-[12.5px] text-muted">
      <label className={clsx("flex items-baseline gap-2", disabled ? "cursor-not-allowed" : "cursor-pointer hover:text-ink")}>
        <input
          type="checkbox"
          checked={included}
          disabled={disabled}
          onChange={(e) => onInclude(e.target.checked)}
          aria-describedby={previewId}
          className="translate-y-[2px]"
        />
        <span>
          Send with your message: <span className="text-ink">{context.label}</span>
        </span>
      </label>
      <pre
        id={previewId}
        aria-label={`What would be sent: ${context.label}`}
        className={clsx(
          "mt-1.5 overflow-auto rounded-[3px] bg-sunken px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed whitespace-pre text-ink",
          expanded ? "max-h-60" : "max-h-24",
          !included && "opacity-60",
        )}
      >
        {expanded ? lines.join("\n") : lines.slice(0, PREVIEW_LINES).join("\n") + (long ? "\n…" : "")}
      </pre>
      <p className="mt-1 flex gap-2 text-[11.5px] text-faint">
        {lines.length} line{lines.length === 1 ? "" : "s"}
        {long && (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            aria-controls={previewId}
            className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
          >
            {expanded ? "Show less" : `Show all ${lines.length} lines`}
          </button>
        )}
      </p>
    </div>
  );
}

/** Before the first message: the mode's empty state and a message box that starts the conversation. */
function NotStarted({
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
  mode: string;
  model?: string;
  prepare: (text: string) => string;
  note?: ComposerNote;
  headerStart?: ReactNode;
  headerActions?: ReactNode;
  placeholder?: string;
  sendLabel?: string;
  onStarted: (conversation: Conversation) => void;
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
  const start = useMutation({
    mutationFn: async (message: string) => {
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
    onSuccess: (conversation) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      onStarted(conversation);
    },
  });
  const send = (text: string): Promise<unknown> => {
    if (inFlight.current) return Promise.reject(new Error("Already sending."));
    inFlight.current = true;
    // Made now, before anything waits: what's sent is what the person saw as they pressed Send.
    return start.mutateAsync(prepare(text)).finally(() => {
      inFlight.current = false;
    });
  };
  const unknownMode = modes.isSuccess && !mode;
  // Shown once, over the message box, whether a starter or a typed message failed.
  const error = unknownMode ? `DataLab has no “${modeId}” mode.` : start.error?.message;

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
          {mode && (
            <EmptyState
              mode={mode.id}
              kind={mode.kind}
              onPick={(text) => send(text).catch(() => undefined)}
              starting={start.isPending}
            />
          )}
        </div>
      </div>
      <ComposerBox
        running={false}
        sending={start.isPending || !mode}
        error={error}
        onSend={send}
        note={note}
        placeholder={placeholder}
        sendLabel={sendLabel}
      />
    </div>
  );
}
