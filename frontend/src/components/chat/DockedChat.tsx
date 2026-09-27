import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useRef, useState } from "react";

import { api, type Conversation } from "@/api/client";

import { Chat, ChatHeader, ComposerBox, EmptyState, useEffortChoice } from "./Chat";

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
 * With `context`, the person can send it along with each message (on by default).
 */
export function DockedChat({
  mode,
  conversationId,
  onConversation,
  context,
  model,
  headerStart,
  headerActions,
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
}) {
  const [started, setStarted] = useState<Conversation | null>(null);
  const [includeContext, setIncludeContext] = useState(true);
  const id = conversationId ?? started?.id;
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations, enabled: Boolean(id) });
  // The list may not have caught up with a conversation just started.
  const conversation = conversations.data?.find((c) => c.id === id) ?? (started?.id === id ? started : undefined);

  const attach = Boolean(context?.text.trim()) && includeContext;
  const prepare = (text: string) => (attach ? withContext(text, context) : text);
  const note = context?.text.trim() ? (
    <label className="mb-2 flex cursor-pointer items-center gap-2 px-1 font-sans text-[12.5px] text-muted hover:text-ink">
      <input type="checkbox" checked={includeContext} onChange={(e) => setIncludeContext(e.target.checked)} />
      Send with your message: <span className="text-ink">{context.label}</span>
    </label>
  ) : undefined;

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
        prepareMessage={context ? prepare : undefined}
        composerNote={note}
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
      onStarted={(conversation) => {
        setStarted(conversation);
        onConversation?.(conversation);
      }}
    />
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
  onStarted,
}: {
  mode: string;
  model?: string;
  prepare: (text: string) => string;
  note?: ReactNode;
  headerStart?: ReactNode;
  headerActions?: ReactNode;
  onStarted: (conversation: Conversation) => void;
}) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 5 * 60_000 });
  const mode = modes.data?.find((m) => m.id === modeId);
  const [effort, setEffort] = useEffortChoice();
  const queryClient = useQueryClient();
  // Made by the first try; a send that failed is tried again in the same conversation.
  const created = useRef<Conversation | null>(null);
  const start = useMutation({
    mutationFn: async (text: string) => {
      created.current ??= await api.createConversation(modeId, model);
      return (await api.send(created.current.id, prepare(text), effort)) ?? created.current;
    },
    onSuccess: (conversation) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      onStarted(conversation);
    },
  });
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
              onPick={(text) => start.mutate(text)}
              starting={start.isPending}
            />
          )}
        </div>
      </div>
      <ComposerBox
        running={false}
        sending={start.isPending || !mode}
        error={error}
        onSend={(text) => start.mutateAsync(text)}
        note={note}
      />
    </div>
  );
}
