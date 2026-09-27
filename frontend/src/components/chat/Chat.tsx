import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, type Conversation, type Effort } from "@/api/client";
import { Button, Chip, Icon, SessionBadge } from "@/components/ui";

import { ApprovalCard } from "./ApprovalCard";
import { Markdown } from "./Markdown";
import { activityRows, nowLine } from "./activity";
import { GroupRow, Marker, NowCard, SayRow, StepRow, Story } from "./Story";
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
  // Follow new steps only while the person is at the bottom: scrolling up to
  // read something shouldn't be undone by the next step arriving.
  const [following, setFollowing] = useState(true);
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

  // A new conversation, or a new question, brings the view back to the bottom.
  useEffect(() => setFollowing(true), [conversation.id, turns.length]);
  useEffect(() => {
    if (following) bottom.current?.scrollIntoView({ block: "end" });
  }, [events.length, following]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line px-8 py-3.5">
        <h1 className="truncate font-serif text-[19px]">{conversation.title}</h1>
        <SessionBadge kind={conversation.kind} />
        <span className="font-mono text-[11.5px] text-faint">{conversation.model}</span>
        <div className="ml-auto flex items-center gap-2">
          {conversation.kind === "data" && <RigorSwitch conversation={conversation} />}
          {headerActions}
        </div>
      </header>
      <div
        className="relative min-h-0 flex-1 overflow-y-auto px-8 py-8"
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
        }}
      >
        <div className="mx-auto flex max-w-[42rem] flex-col gap-14">
          {turns.length === 0 && <EmptyState conversation={conversation} onPick={setSuggestion} />}
          {turns.map((turn, index) => (
            <TurnView key={index} turn={turn} conversationId={conversation.id} running={running} />
          ))}
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
      className="flex cursor-pointer items-center gap-2 font-sans text-[13px] text-muted hover:text-ink"
      title="After each answer that did some work, the agent's work is reviewed against a checklist: traced claims, the plan, causal language, sample sizes, uncertainty, privacy. It roughly doubles the time and cost of each answer."
    >
      <input
        type="checkbox"
        className="peer sr-only"
        checked={conversation.rigor_review}
        onChange={() => toggle.mutate()}
        disabled={toggle.isPending}
      />
      <span
        aria-hidden="true"
        className="relative h-4 w-7 rounded-full bg-line transition-colors peer-checked:bg-ink peer-focus-visible:outline-1 peer-focus-visible:outline-ink after:absolute after:top-0.5 after:left-0.5 after:size-3 after:rounded-full after:bg-surface after:transition-transform peer-checked:after:translate-x-3"
      />
      Rigor review
    </label>
  );
}

function EmptyState({ conversation, onPick }: { conversation: Conversation; onPick: (s: { text: string }) => void }) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const mode = modes.data?.find((m) => m.id === conversation.mode);
  const data = conversation.kind === "data";
  return (
    <div className="mt-6 flex flex-col gap-10">
      <div>
        <p className="dl-label">{mode?.label ?? "New conversation"}</p>
        <h2 className="mt-3 font-serif text-[42px] leading-[1.1] tracking-[-0.01em] text-balance">What would you like to find out?</h2>
        <p className="mt-4 max-w-[52ch] font-serif text-[18px] leading-relaxed text-muted">{mode?.description}</p>
      </div>
      <ul className="grid gap-x-6 gap-y-3 border-t border-line pt-4 font-sans text-[13px] text-muted sm:grid-cols-3">
        {(data
          ? [
              ["db", "Reads the IHS database, read-only"],
              ["eye", "Shows you everything it reads and runs"],
              ["lock", "No internet: nothing leaves this computer"],
            ]
          : [
              ["globe", "Searches the web and reads papers"],
              ["eye", "Shows you everything it reads"],
              ["lock", "Has no access to study data"],
            ]
        ).map(([icon, text]) => (
          <li key={text} className="flex items-start gap-2">
            <Icon name={icon as "db"} size={14} className={clsx("mt-0.5 shrink-0", data ? "text-data" : "text-research")} />
            {text}
          </li>
        ))}
      </ul>
      {mode && mode.starters.length > 0 && (
        <div>
          <p className="dl-label mb-2">Try one of these, or ask your own</p>
          <ul className="border-t border-line">
            {mode.starters.map((starter) => (
              <li key={starter} className="border-b border-line">
                <button
                  onClick={() => onPick({ text: starter })}
                  className="group flex w-full items-baseline gap-4 py-3.5 text-left font-serif text-[18.5px] leading-snug text-ink"
                >
                  <span className="flex-1 group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">{starter}</span>
                  <Icon name="chevron" size={14} className="shrink-0 text-faint group-hover:text-ink" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function TurnView({ turn, conversationId, running }: { turn: Turn; conversationId: string; running: boolean }) {
  const answer = finalAnswer(turn);
  const live = turn.status === "running" && running;
  const rows = activityRows(turn.items, live);
  const story = rows.filter((row) => row.type !== "review");
  const reviews = turn.items.filter((item): item is Extract<Item, { kind: "review" }> => item.kind === "review");
  const reasoning = [...turn.items].reverse().find((item) => item.kind === "reasoning");
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stop(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <article className="flex flex-col gap-5">
      {turn.userText && (
        <Question text={turn.userText} />
      )}
      <Story
        rows={story}
        renderRow={(row) => {
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
        }}
      />
      {live && !answer && (
        <NowCard
          line={nowLine(rows, reasoning?.kind === "reasoning" ? reasoning.text : "")}
          waiting={waitingFor(rows)}
          onStop={() => stop.mutate()}
          stopping={stop.isPending}
        />
      )}
      {answer && <AnswerCard answer={answer} trace={turn.trace} streaming={live} />}
      {reviews.map((review, i) => (
        <ReviewBox key={`review-${i}`} review={review} conversationId={conversationId} running={running} />
      ))}
      {turn.status === "interrupted" && (
        <p className="font-serif text-[16px] text-muted italic">Stopped. Anything it saved is in History.</p>
      )}
    </article>
  );
}

/** The person's question, set large; a long, pasted one reads as text, not as a heading. */
function Question({ text }: { text: string }) {
  if (text.length > 220) {
    return <p className="font-serif text-[19px] leading-relaxed break-words whitespace-pre-wrap text-ink">{text}</p>;
  }
  return (
    <h2 className="font-serif text-[28px] leading-[1.18] tracking-[-0.005em] break-words whitespace-pre-wrap text-balance text-ink">
      {text}
    </h2>
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
function AnswerCard({ answer, trace, streaming }: { answer: string; trace: Turn["trace"]; streaming: boolean }) {
  return (
    <section className="mt-2 rounded-[4px] border border-line border-t-2 border-t-ink bg-surface px-6 pt-4 pb-5 [&_.prose-datalab]:text-[1.2rem]">
      <h3 className="mb-3 flex items-center gap-2 font-sans text-[12px] font-semibold tracking-[0.08em] text-ink uppercase">
        {streaming ? "Writing the answer…" : "Answer"}
      </h3>
      <Markdown text={answer} />
      {trace && !streaming && (
        <div className="mt-6 flex flex-wrap items-center gap-1.5 border-t border-line pt-3">
          <TraceChip trace={trace} />
        </div>
      )}
    </section>
  );
}

/** Which numbers in the answer came from something the turn produced. */
function TraceChip({ trace }: { trace: NonNullable<Turn["trace"]> }) {
  const how =
    "DataLab looks for each number in this turn's query results, command output, and data files. A match only means the number appears there, not that it's right.";
  if (trace.untraced.length === 0) {
    return (
      <Chip tone="good" title={how}>
        <Icon name="check" size={12} />{" "}
        {trace.numbers === 1 ? "the 1 number" : `all ${trace.numbers} numbers`} found in this turn's results
      </Chip>
    );
  }
  return (
    <>
      <Chip tone="attn" title={how}>
        {trace.untraced.length} of {trace.numbers} number{trace.numbers === 1 ? "" : "s"} not found in this turn's results
      </Chip>
      {trace.untraced.slice(0, 8).map((n) => (
        <Chip key={n} tone="attn" title="Not in this turn's query results, command output, or data files: check it">
          {n}
        </Chip>
      ))}
      {trace.untraced.length > 8 && <Chip tone="attn">+{trace.untraced.length - 8}</Chip>}
    </>
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
  const [open, setOpen] = useState(true);
  const reviewing = review.status === "running" && running;
  return (
    <section className="border-y border-line">
      <div className="flex items-start gap-3 py-3">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="group flex flex-1 items-start gap-3 text-left">
        {reviewing ? <Marker tone="now" open={null} /> : <Marker tone="done" open={open} />}
        <span className="flex-1">
          <span className="block font-sans text-[14.5px] text-ink">Rigor review</span>
          <span className="block font-serif text-[15.5px] text-muted italic">
            {reviewing
              ? "The agent is checking its own work against the lab's checklist…"
              : review.status === "done"
                ? "The agent's check of its own work: a second opinion, not proof."
                : review.status === "stopped"
                  ? "Stopped before it finished."
                  : "Didn't finish."}
          </span>
        </span>
      </button>
        {reviewing && (
          <button
            type="button"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:text-danger disabled:opacity-45"
          >
            Stop the review
          </button>
        )}
      </div>
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
  suggestion,
}: {
  conversation: Conversation;
  running: boolean;
  suggestion: { text: string } | null;
}) {
  const [text, setText] = useState("");
  // The box grows with what's typed (wrapped lines too), up to about eight lines.
  const box = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
  }, [text]);
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
    <footer className="px-8 pt-2 pb-6">
      <div className="mx-auto max-w-[42rem]">
        {send.error && <p className="mb-2 text-sm text-danger">{send.error.message}</p>}
        {/* The hint sits under the box, so the box itself asks a plain question. */}
        <div className="flex items-end gap-2 rounded-[4px] border border-line bg-field p-2 pl-3 transition-colors focus-within:border-ink focus-within:shadow-[0_0_0_1px_var(--color-ink)]">
          <textarea
            ref={box}
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
            placeholder={running ? "The agent is working. You can stop it, or wait to ask more." : "Ask a question, or say what to do next"}
            className="min-h-10 flex-1 resize-none bg-transparent py-1.5 font-sans text-[15px] leading-relaxed outline-none placeholder:text-faint"
          />
          <select
            value={effort}
            onChange={(e) => setEffort(e.target.value as Effort)}
            aria-label="How hard the agent thinks"
            className="self-center bg-transparent py-1.5 font-sans text-[12.5px] text-muted hover:text-ink"
            title="How hard the agent thinks"
          >
            <option value="low">Quick</option>
            <option value="medium">Balanced</option>
            <option value="high">Thorough</option>
          </select>
          {running ? (
            <Button variant="secondary" onClick={() => stop.mutate()} disabled={stop.isPending}>
              <Icon name="stop" size={14} /> Stop
            </Button>
          ) : (
            <Button variant="primary" onClick={submit} disabled={!text.trim() || send.isPending}>
              <Icon name="send" size={14} /> Send
            </Button>
          )}
        </div>
        <p className="mt-1.5 px-1 font-sans text-[11.5px] text-faint">
          Enter to send · Shift+Enter for a new line · the agent shows its steps as it works
        </p>
      </div>
    </footer>
  );
}
