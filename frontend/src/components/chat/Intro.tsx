// What a conversation with no questions yet shows: the Workspace's EmptyState,
// or a docked chat's CompactIntro, each with starters to pick.
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useId, useState } from "react";

import { api } from "@/api/client";
import { Icon } from "@/components/ui";
import type { AnyIcon } from "@/components/ui/Icon";

import { ASSISTANTS, type AssistantId } from "./assistants";
import { HOVER_TITLE } from "./Story";

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
