import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, use, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, type Conversation, type Effort } from "@/api/client";
import { Button, Chip, FileGlyph, Icon, SessionBadge } from "@/components/ui";
import { OpenFileContext, workspaceFile } from "@/lib/files";

import { ApprovalCard } from "./ApprovalCard";
import { Markdown } from "./Markdown";
import { activityRows, answerOf, nowLine, type Row } from "./activity";
import { GroupRow, Marker, NowCard, SayRow, StepRow, Story } from "./Story";
import { buildTranscript, canContinue, type Item, type ModelStatus, type Turn } from "./transcript";
import { useConversationEvents } from "./useConversationEvents";

// Events that start or end a turn or its review: DataLab's busy flag changes.
const TURN_EVENTS = new Set(["user_message", "turn_started", "turn_finished", "review_started", "review_finished", "turn_done"]);

/** The shared chat. Every tab that needs an agent uses this component. */
export function Chat({
  conversation,
  headerStart,
  headerActions,
}: {
  conversation: Conversation;
  headerStart?: ReactNode;
  headerActions?: ReactNode;
}) {
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

  // A title written from the first question, or a rename in another window.
  const lastTitleEvent = events.findLast((e) => e.type === "title_changed")?.seq;
  useEffect(() => {
    if (lastTitleEvent !== undefined) queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [lastTitleEvent, queryClient]);

  // A new conversation, or a new question, brings the view back to the bottom.
  useEffect(() => setFollowing(true), [conversation.id, turns.length]);
  useEffect(() => {
    if (following) bottom.current?.scrollIntoView({ block: "end" });
  }, [events.length, following]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-3 sm:px-8">
        {headerStart}
        <Title conversation={conversation} />
        <SessionBadge kind={conversation.kind} />
        <span className="font-mono text-[11.5px] text-faint">{conversation.model}</span>
        <div className="ml-auto flex items-center gap-2">
          {conversation.kind === "data" && <RigorSwitch conversation={conversation} />}
          {headerActions}
        </div>
      </header>
      <div
        className="relative min-h-0 flex-1 overflow-y-auto px-4 py-8 sm:px-8"
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
        }}
      >
        <div className="mx-auto flex max-w-[56rem] flex-col gap-14 2xl:max-w-[64rem]">
          {turns.length === 0 && <EmptyState conversation={conversation} onPick={setSuggestion} />}
          {turns.map((turn, index) => (
            <TurnView
              key={index}
              turn={turn}
              conversationId={conversation.id}
              running={running}
              last={index === turns.length - 1}
            />
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
export function Title({ conversation }: { conversation: Conversation }) {
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
        className="min-w-0 border-b border-ink bg-transparent font-serif text-[19px] outline-none"
      />
    );
  }
  return (
    <h1 className="min-w-0 truncate font-serif text-[19px]">
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
    </h1>
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
              ["db", "Queries the IHS database, read-only"],
              ["eye", "Shows you everything it reads and runs"],
              ["lock", "Websites blocked; the model is U-M's approved GPT service"],
            ]
          : [
              ["globe", "Searches the web and reads papers"],
              ["eye", "Shows you everything it reads"],
              ["lock", "No connection to the study database"],
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
  const storyRows = rows.filter((row) => row.type !== "review");
  const reviews = turn.items.filter((item): item is Extract<Item, { kind: "review" }> => item.kind === "review");
  const reasoning = [...turn.items].reverse().find((item) => item.kind === "reasoning");
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stop(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const story = (
    <Story
      rows={storyRows}
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
  );
  // Completed with an answer: the answer leads, the work behind it folds away.
  // A failed or stopped turn keeps its whole story in view.
  const finished = Boolean(answer) && turn.status === "completed" && !live;
  return (
    <article className="flex flex-col gap-5">
      {turn.userText && <Question text={turn.userText} continues={turn.continues} />}
      {!finished && story}
      {live && !answer && (
        <NowCard
          line={
            turn.model?.state === "retrying"
              ? retryLine(turn.model)
              : nowLine(rows, reasoning?.kind === "reasoning" ? reasoning.text : "")
          }
          waiting={waitingFor(rows)}
          onStop={() => stop.mutate()}
          stopping={stop.isPending}
        />
      )}
      {answer && <AnswerCard answer={answer} trace={turn.trace} streaming={live} />}
      {/* Only the latest review: one run again replaces one that didn't finish. */}
      {reviews.slice(-1).map((review) => (
        <ReviewBox key={`review-${reviews.length}`} review={review} conversationId={conversationId} running={running} last={last} />
      ))}
      {finished && <MadeHere items={turn.items} conversationId={conversationId} />}
      {finished && <HowItWasMade rows={storyRows}>{story}</HowItWasMade>}
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
  // Failed steps and error notices are named on the row, so folding never hides them.
  const errors =
    steps.filter((step) => step.tone === "error").length +
    rows.filter((row) => row.type === "notice" && row.tone === "error").length;
  const plan = rows.find((row) => row.type === "approval" && row.approval.approvalKind === "analysis_plan");
  const facts = [
    steps.length > 0 && `${steps.length} step${steps.length === 1 ? "" : "s"}`,
    count("db") && `${count("db")} quer${count("db") === 1 ? "y" : "ies"}`,
    count("book") && `${count("book")} lab guide${count("book") === 1 ? "" : "s"} read`,
    files && `${files} file${files === 1 ? "" : "s"} changed`,
  ].filter(Boolean) as string[];
  const planChip =
    plan?.type !== "approval"
      ? null
      : plan.approval.frozen
        ? { text: "plan approved and frozen", tone: "you" as const }
        : plan.approval.state === "declined"
          ? { text: "plan not approved", tone: "attn" as const }
          : plan.approval.state === "withdrawn"
            ? { text: "plan withdrawn", tone: "attn" as const }
            : null;
  return (
    <section className="border-y border-line">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="group flex w-full items-start gap-3 py-3 text-left">
        <Marker tone="done" open={open} />
        <span className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="font-sans text-[14.5px] text-ink">How this answer was made</span>
          <span className="flex flex-wrap gap-1.5">
            {facts.map((fact) => (
              <Chip key={fact}>{fact}</Chip>
            ))}
            {planChip && <Chip tone={planChip.tone}>{planChip.text}</Chip>}
            {errors > 0 && (
              <Chip tone="bad">
                {errors} error{errors === 1 ? "" : "s"} along the way
              </Chip>
            )}
          </span>
        </span>
      </button>
      {open && <div className="pb-4 pl-[19px]">{children}</div>}
    </section>
  );
}

/** The first line of Markdown text, without its markup. */
function firstLine(text: string): string {
  const line = text.split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  return line.replace(/^#+\s*|^[-*]\s+|^\d+\.\s+/, "").replace(/\*\*|`/g, "");
}

/** The person's question, set large; a long, pasted one reads as text, not as a heading. */
function Question({ text, continues }: { text: string; continues?: boolean }) {
  if (continues) return <p className="dl-label">Continued after an interruption</p>;
  if (text.length > 220) {
    return <p className="font-serif text-[19px] leading-relaxed break-words whitespace-pre-wrap text-ink">{text}</p>;
  }
  return (
    <h2 className="font-serif text-[28px] leading-[1.18] tracking-[-0.005em] break-words whitespace-pre-wrap text-balance text-ink">
      {text}
    </h2>
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
      <Chip title={how}>
        {trace.numbers === 1 ? "the 1 number" : `all ${trace.numbers} numbers`} matched to this turn's outputs
      </Chip>
    );
  }
  return (
    <>
      <Chip tone="attn" title={how}>
        {trace.untraced.length} of {trace.numbers} number{trace.numbers === 1 ? "" : "s"} not matched to this turn's outputs
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
          <span className="block font-sans text-[14.5px] text-ink">Rigor review</span>
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
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:text-danger disabled:opacity-45"
          >
            Stop the review
          </button>
        )}
        {unfinished && last && (
          <button
            type="button"
            onClick={() => again.mutate()}
            disabled={running || again.isPending || again.isSuccess}
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink disabled:opacity-45"
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
    <footer className="px-4 pt-2 pb-6 sm:px-8">
      <div className="mx-auto max-w-[56rem] 2xl:max-w-[64rem]">
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
