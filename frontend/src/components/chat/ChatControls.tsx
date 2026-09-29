// The controls over a chat: its title, which session it is, the model and how
// hard it thinks, and the conversation's switches (Express, Rigor review).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useRef, useState } from "react";

import { api, type Conversation, type Effort } from "@/api/client";
import { Chip, InfoTip, SessionBadge } from "@/components/ui";

import { ASSISTANTS, type AssistantId } from "./assistants";

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
  otherMode,
}: {
  assistant: AssistantId;
  /** The conversation's mode, when it isn't the tab's own (a Data engineering chat in Pipelines). */
  otherMode?: string;
  kind: "data" | "research";
  /** The conversation's title, once it has started. */
  title?: ReactNode;
  model: string;
  effort: Effort;
  onEffort: (effort: Effort) => void;
  actions?: ReactNode;
}) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes, enabled: Boolean(otherMode) });
  const otherLabel = otherMode ? (modes.data?.find((m) => m.id === otherMode)?.label ?? otherMode) : null;
  return (
    <header className="@container flex flex-col gap-0.5 border-b border-line px-4 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <h2 className="min-w-0 truncate font-sans text-[14px] font-semibold text-ink">{ASSISTANTS[assistant].title}</h2>
        {otherLabel && (
          <span title="This conversation started in another mode, and keeps its instructions and tools">
            <Chip>{otherLabel}</Chip>
          </span>
        )}
        <SessionBadge kind={kind} short />
        <div className="ml-auto flex shrink-0 items-center gap-1">{actions}</div>
      </div>
      <div className="flex min-w-0 items-center gap-2 font-mono text-[11.5px] text-faint">
        {title && <span className="min-w-0 flex-1 truncate">{title}</span>}
        <span className="flex shrink-0 items-center gap-1.5">
          {model && (
            <>
              {model}
              <span aria-hidden>·</span>
            </>
          )}
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

export function RigorSwitch({ conversation }: { conversation: Conversation }) {
  return (
    <ConversationSwitch
      label="Rigor review"
      term="rigor-review"
      on={conversation.rigor_review}
      change={() => api.setRigorReview(conversation.id, !conversation.rigor_review)}
      busy={conversation.busy}
    />
  );
}

// What the Express label says on hover.
export const EXPRESS_TITLE = "Asked with Express on: low effort, no plans or confirmations. Same data access and checks.";

/**
 * Express: quick answers in any mode (low effort, no plans or confirmations;
 * the same data access and checks). DataLab switches the rigor review off
 * when it's switched on, and the other way round.
 */
export function ExpressSwitch({ conversation, compact = false }: { conversation: Conversation; compact?: boolean }) {
  return (
    <ConversationSwitch
      label="Express"
      term="express"
      on={conversation.express}
      change={() => api.setExpress(conversation.id, !conversation.express)}
      busy={conversation.busy}
      compact={compact}
    />
  );
}

/** A switch in a conversation's header (Express, Rigor review), with what it means. */
function ConversationSwitch({
  label,
  term,
  on,
  change,
  busy = false,
  compact = false,
}: {
  label: string;
  term: "express" | "rigor-review";
  on: boolean;
  change: () => Promise<unknown>;
  /** The agent is working: a change applies from the next message (DataLab keeps each turn's own). */
  busy?: boolean;
  compact?: boolean;
}) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: change,
    // Both switches come back: one switched on may have switched the other off.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <span className="flex items-center gap-1">
      <label
        title={busy ? "Applies from your next message: the answer in progress keeps how it started" : undefined}
        className={clsx(
          "flex items-center gap-2 font-sans text-muted hover:text-ink has-[:disabled]:cursor-not-allowed",
          compact ? "text-[12px]" : "text-[13px]",
        )}
      >
        <input type="checkbox" className="peer sr-only" checked={on} onChange={() => toggle.mutate()} disabled={toggle.isPending} />
        <span
          aria-hidden="true"
          className="relative h-4 w-7 rounded-full bg-line transition-colors peer-checked:bg-ink peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink peer-disabled:opacity-70 after:absolute after:top-0.5 after:left-0.5 after:size-3 after:rounded-full after:bg-surface after:transition-transform peer-checked:after:translate-x-3"
        />
        {label}
      </label>
      <InfoTip term={term} align="end" />
    </span>
  );
}

/** How hard the agent thinks, for the next message. */
export function EffortSelect({ effort, onChange }: { effort: Effort; onChange: (effort: Effort) => void }) {
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
