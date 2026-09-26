import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { api, type Conversation, type Effort } from "@/api/client";
import { Button, SessionBadge } from "@/components/ui";

import { ApprovalCard } from "./ApprovalCard";
import { Markdown } from "./Markdown";
import { buildTranscript, finalAnswer, type Item, type Turn } from "./transcript";
import { useConversationEvents } from "./useConversationEvents";

// Events that start or end a turn or its review: DataLab's busy flag changes.
const TURN_EVENTS = new Set(["user_message", "turn_started", "turn_finished", "review_started", "review_finished", "turn_done"]);

/** The shared chat. Every tab that needs an agent uses this component. */
export function Chat({ conversation, headerActions }: { conversation: Conversation; headerActions?: ReactNode }) {
  const events = useConversationEvents(conversation.id);
  const turns = useMemo(() => buildTranscript(events), [events]);
  const last = turns.at(-1);
  const reviewing = last?.items.some((i) => i.kind === "review" && i.status === "running") ?? false;
  const transcriptRunning = last?.status === "running" || reviewing;
  const bottom = useRef<HTMLDivElement>(null);
  const [suggestion, setSuggestion] = useState<{ text: string } | null>(null);
  const queryClient = useQueryClient();

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

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [events.length]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-3 border-b border-line px-5 py-3">
        <h1 className="truncate font-semibold">{conversation.title}</h1>
        <SessionBadge kind={conversation.kind} />
        <span className="text-xs text-muted">{conversation.model}</span>
        <div className="ml-auto flex items-center gap-2">
          {conversation.kind === "data" && <RigorSwitch conversation={conversation} />}
          {headerActions}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {turns.length === 0 && <EmptyState conversation={conversation} onPick={setSuggestion} />}
          {turns.map((turn, index) => (
            <TurnView key={index} turn={turn} conversationId={conversation.id} running={running} />
          ))}
          <div ref={bottom} />
        </div>
      </div>
      <Composer conversation={conversation} running={running} suggestion={suggestion} />
    </div>
  );
}

function RigorSwitch({ conversation }: { conversation: Conversation }) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => api.setRigorReview(conversation.id, !conversation.rigor_review),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <label
      className="flex items-center gap-1.5 text-xs text-muted"
      title="After each answer that did some work, the agent's work is reviewed against a checklist: traced claims, the plan, causal language, sample sizes, uncertainty, privacy. It roughly doubles the time and cost of each answer."
    >
      <input type="checkbox" checked={conversation.rigor_review} onChange={() => toggle.mutate()} disabled={toggle.isPending} />
      Rigor review
    </label>
  );
}

function EmptyState({ conversation, onPick }: { conversation: Conversation; onPick: (s: { text: string }) => void }) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const mode = modes.data?.find((m) => m.id === conversation.mode);
  return (
    <div className="mt-16 text-center text-muted">
      <p className="text-lg text-ink">What would you like to find out?</p>
      <p className="mt-2 text-sm">
        {conversation.kind === "data"
          ? "The agent can query the IHS database (read-only) and analyse the results. It has no internet."
          : "The agent can search the web and read papers. It has no access to study data."}
      </p>
      {mode && mode.starters.length > 0 && (
        <div className="mx-auto mt-6 flex max-w-xl flex-col gap-2">
          {mode.starters.map((starter) => (
            <button
              key={starter}
              onClick={() => onPick({ text: starter })}
              className="rounded-xl border border-line bg-surface px-4 py-2 text-left text-sm text-ink hover:bg-sunken"
            >
              {starter}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TurnView({ turn, conversationId, running }: { turn: Turn; conversationId: string; running: boolean }) {
  const answer = finalAnswer(turn);
  // Notices and approval cards are shown on their own, above the work log.
  const working = turn.items.filter(
    (item) =>
      item.kind !== "notice" &&
      item.kind !== "approval" &&
      item.kind !== "review" &&
      !(item.kind === "message" && item.text === answer && answer),
  );
  return (
    <article className="flex flex-col gap-3">
      {turn.userText && (
        <div className="self-end whitespace-pre-wrap rounded-2xl bg-accent-soft px-4 py-2.5 text-sm">
          {turn.userText}
        </div>
      )}
      {turn.items
        .filter((item) => item.kind === "notice")
        .map((item, index) => (
          <p key={index} className={item.kind === "notice" && item.tone === "error" ? "text-sm text-danger" : "rounded-lg bg-sunken px-3 py-2 text-sm text-muted"}>
            {item.kind === "notice" ? item.text : null}
          </p>
        ))}
      {working.length > 0 && <WorkLog items={working} running={turn.status === "running"} />}
      {turn.items.map((item) =>
        item.kind === "approval" ? <ApprovalCard key={item.id} conversationId={conversationId} approval={item} /> : null,
      )}
      {answer && <Markdown text={answer} />}
      {answer && turn.trace && <TraceLine trace={turn.trace} />}
      {turn.items.map((item, i) =>
        item.kind === "review" ? <ReviewBox key={`review-${i}`} review={item} conversationId={conversationId} running={running} /> : null,
      )}
      {turn.status === "interrupted" && <p className="text-sm text-muted">Stopped.</p>}
    </article>
  );
}

/** Which numbers in the answer came from something the turn produced. */
function TraceLine({ trace }: { trace: NonNullable<Turn["trace"]> }) {
  const how =
    "DataLab looks for each number in this turn's query results, command output, and data files. A match only means the number appears there, not that it's right.";
  if (trace.untraced.length === 0) {
    return (
      <p className="text-xs text-muted" title={how}>
        All {trace.numbers} numbers were found in this turn's output.
      </p>
    );
  }
  return (
    <p className="text-xs text-research" title={how}>
      {trace.untraced.length} of {trace.numbers} numbers weren't found in this turn's query results, command output,
      or data files; check them: <span className="font-mono">{trace.untraced.slice(0, 12).join(", ")}</span>
      {trace.untraced.length > 12 ? ", …" : ""}
    </p>
  );
}

function ReviewBox({
  review,
  conversationId,
  running,
}: {
  review: Extract<Item, { kind: "review" }>;
  conversationId: string;
  running: boolean;
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
  return (
    <details open className="rounded-xl border border-line bg-surface px-4 py-2 text-sm">
      <summary className="cursor-pointer font-medium">
        🔎 Rigor review
        {review.status === "running" && !running && (
          <span className="ml-2 text-xs font-normal text-danger">didn't finish</span>
        )}
        {review.status === "running" && running && (
          <>
            <span className="ml-2 text-xs font-normal text-muted">reviewing…</span>
            <Button variant="ghost" className="ml-2 px-2 py-0.5 text-xs" onClick={() => stop.mutate()} disabled={stop.isPending}>
              Stop the review
            </Button>
          </>
        )}
        {review.status === "failed" && <span className="ml-2 text-xs font-normal text-danger">didn't finish</span>}
        {review.status === "stopped" && <span className="ml-2 text-xs font-normal text-muted">stopped</span>}
      </summary>
      {review.text && review.status === "done" && (
        <div className="mt-2">
          <Markdown text={review.text} />
          <div className="mt-2 flex justify-end">
            <Button
              onClick={() => address.mutate()}
              disabled={running || review.status !== "done" || address.isPending || address.isSuccess}
              title={running ? "Wait until the agent has finished" : undefined}
            >
              Ask the agent to address these
            </Button>
          </div>
          {address.error && <p className="mt-1 text-right text-xs text-danger">{address.error.message}</p>}
        </div>
      )}
    </details>
  );
}

function WorkLog({ items, running }: { items: Item[]; running: boolean }) {
  const [open, setOpen] = useState(false);
  const commands = items.filter((i) => i.kind === "command").length;
  const tools = items.filter((i) => i.kind === "tool").length;
  const edits = items.reduce((n, i) => n + (i.kind === "files" ? i.paths.length : 0), 0);
  const latest = [...items].reverse().find((i) => i.kind === "message");
  return (
    <div className="rounded-xl border border-line bg-surface">
      <button
        className="flex w-full items-center justify-between px-4 py-2 text-left text-sm text-muted"
        onClick={() => setOpen(!open)}
      >
        <span className="truncate">
          {running ? "Working… " : "Worked · "}
          {[
            commands && `${commands} command${commands > 1 ? "s" : ""}`,
            tools && `${tools} database step${tools > 1 ? "s" : ""}`,
            edits && `${edits} file edit${edits > 1 ? "s" : ""}`,
          ]
            .filter(Boolean)
            .join(", ")}
          {running && latest?.kind === "message" && latest.text && (
            <span className="ml-2 text-ink">{latest.text.slice(-140)}</span>
          )}
        </span>
        <span aria-hidden>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ol className="flex flex-col gap-2 border-t border-line px-4 py-3 text-sm">
          {items.map((item, index) => (
            <li key={index}>
              <WorkItem item={item} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function WorkItem({ item }: { item: Item }) {
  switch (item.kind) {
    case "message":
      return <p className="text-muted">{item.text}</p>;
    case "reasoning":
      return <p className="italic text-muted">{item.text}</p>;
    case "command":
      return (
        <details>
          <summary className="cursor-pointer font-mono text-xs">
            <span className={clsx(item.exitCode ? "text-danger" : "text-data")}>$</span> {item.command}
            {item.status === "running" && <span className="ml-2 text-muted">(running)</span>}
          </summary>
          {item.output && (
            <pre className="mt-1 max-h-64 overflow-auto rounded bg-sunken p-2 font-mono text-xs">{item.output}</pre>
          )}
        </details>
      );
    case "tool":
      return (
        <details>
          <summary className="cursor-pointer text-xs">
            <span className={clsx(item.status === "failed" ? "text-danger" : "text-data")}>●</span> {item.tool}
            {item.status === "failed" && <span className="ml-2 text-danger">failed</span>}
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-sunken p-2 font-mono text-xs">
            {JSON.stringify(item.arguments, null, 2)}
            {item.error ? `\n\n${item.error}` : ""}
          </pre>
        </details>
      );
    case "approval":
    case "review":
      return null;
    case "files":
      return (
        <p className="text-xs text-muted">
          Edited <span className="font-mono">{item.paths.map((p) => p.replace(/^\/work\//, "")).join(", ")}</span>
        </p>
      );
    case "notice":
      return <p className={item.tone === "error" ? "text-danger" : "text-muted"}>{item.text}</p>;
  }
}

function Composer({
  conversation,
  running,
  suggestion,
}: {
  conversation: Conversation;
  running: boolean;
  suggestion: { text: string } | null;
}) {
  const [text, setText] = useState("");
  // A starter prompt the person picked goes into the box, for them to edit and send.
  useEffect(() => {
    // Never over what the person has already typed.
    if (suggestion) setText((current) => (current.trim() ? current : suggestion.text));
  }, [suggestion]);
  const [effort, setEffort] = useState<Effort>("medium");
  const queryClient = useQueryClient();
  const send = useMutation({
    mutationFn: () => api.send(conversation.id, text.trim(), effort),
    onSuccess: () => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const stop = useMutation({
    mutationFn: () => api.stop(conversation.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });

  const submit = () => {
    if (text.trim() && !running && !send.isPending) send.mutate();
  };

  return (
    <footer className="border-t border-line px-5 py-3">
      <div className="mx-auto max-w-3xl">
        {send.error && <p className="mb-2 text-sm text-danger">{send.error.message}</p>}
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 focus-within:border-accent">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={Math.min(8, Math.max(1, text.split("\n").length))}
            placeholder={running ? "The agent is working…" : "Ask a question (Shift+Enter for a new line)"}
            className="min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none"
          />
          <select
            value={effort}
            onChange={(e) => setEffort(e.target.value as Effort)}
            className="rounded-lg bg-transparent px-1 py-1.5 text-xs text-muted"
            title="How hard the agent thinks"
          >
            <option value="low">Quick</option>
            <option value="medium">Balanced</option>
            <option value="high">Thorough</option>
          </select>
          {running ? (
            <Button variant="danger" onClick={() => stop.mutate()} disabled={stop.isPending}>
              Stop
            </Button>
          ) : (
            <Button variant="primary" onClick={submit} disabled={!text.trim() || send.isPending}>
              Send
            </Button>
          )}
        </div>
      </div>
    </footer>
  );
}
