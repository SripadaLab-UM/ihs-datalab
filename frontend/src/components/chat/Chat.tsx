import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type Conversation, type Effort } from "@/api/client";
import { Button, SessionBadge } from "@/components/ui";

import { Markdown } from "./Markdown";
import { buildTranscript, finalAnswer, type Item, type Turn } from "./transcript";
import { useConversationEvents } from "./useConversationEvents";

/** The shared chat. Every tab that needs an agent uses this component. */
export function Chat({ conversation }: { conversation: Conversation }) {
  const events = useConversationEvents(conversation.id);
  const turns = useMemo(() => buildTranscript(events), [events]);
  const running = turns.at(-1)?.status === "running";
  const bottom = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // When a turn finishes, refresh what depends on it (busy dots, data accessed).
  useEffect(() => {
    if (!running) {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["data-accessed", conversation.id] });
    }
  }, [running, conversation.id, queryClient]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [events.length]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-3 border-b border-line px-5 py-3">
        <h1 className="truncate font-semibold">{conversation.title}</h1>
        <SessionBadge kind={conversation.kind} />
        <span className="text-xs text-muted">{conversation.model}</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {turns.length === 0 && <EmptyState kind={conversation.kind} />}
          {turns.map((turn, index) => (
            <TurnView key={index} turn={turn} />
          ))}
          <div ref={bottom} />
        </div>
      </div>
      <Composer conversation={conversation} running={running} />
    </div>
  );
}

function EmptyState({ kind }: { kind: "data" | "research" }) {
  return (
    <div className="mt-16 text-center text-muted">
      <p className="text-lg text-ink">What would you like to find out?</p>
      <p className="mt-2 text-sm">
        {kind === "data"
          ? "The agent can query the IHS database (read-only) and analyse the results. It has no internet."
          : "The agent can search the web and read papers. It has no access to study data."}
      </p>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const answer = finalAnswer(turn);
  const working = turn.items.filter((item) => !(item.kind === "message" && item.text === answer && answer));
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
      {answer && <Markdown text={answer} />}
      {turn.status === "interrupted" && <p className="text-sm text-muted">Stopped.</p>}
    </article>
  );
}

function WorkLog({ items, running }: { items: Item[]; running: boolean }) {
  const [open, setOpen] = useState(false);
  const commands = items.filter((i) => i.kind === "command").length;
  const tools = items.filter((i) => i.kind === "tool").length;
  const latest = [...items].reverse().find((i) => i.kind === "message");
  return (
    <div className="rounded-xl border border-line bg-surface">
      <button
        className="flex w-full items-center justify-between px-4 py-2 text-left text-sm text-muted"
        onClick={() => setOpen(!open)}
      >
        <span className="truncate">
          {running ? "Working… " : "Worked · "}
          {[commands && `${commands} command${commands > 1 ? "s" : ""}`, tools && `${tools} database step${tools > 1 ? "s" : ""}`]
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
    case "notice":
      return <p className={item.tone === "error" ? "text-danger" : "text-muted"}>{item.text}</p>;
  }
}

function Composer({ conversation, running }: { conversation: Conversation; running: boolean }) {
  const [text, setText] = useState("");
  const [effort, setEffort] = useState<Effort>("medium");
  const queryClient = useQueryClient();
  const send = useMutation({
    mutationFn: () => api.send(conversation.id, text.trim(), effort),
    onSuccess: () => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const stop = useMutation({ mutationFn: () => api.stop(conversation.id) });

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
