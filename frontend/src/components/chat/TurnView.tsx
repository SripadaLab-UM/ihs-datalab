// One turn of a conversation as the chat shows it: the question, the story of
// the work while it runs, then the answer with the work folded behind it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, use, useEffect, useState } from "react";

import { api } from "@/api/client";
import { Button, Chip, FileGlyph } from "@/components/ui";
import { OpenFileContext, workspaceFile } from "@/lib/files";

import { ApprovalCard } from "./ApprovalCard";
import { activityRows, answerOf, nowLine, type Row } from "./activity";
import { AnswerCard, ReviewBox } from "./Answer";
import { CompactContext } from "./assistants";
import { EXPRESS_TITLE } from "./ChatControls";
import { checkLines, failureChip, failures } from "./checks";
import { KbSuggestionCard } from "./KbSuggestionCard";
import { SendingLine } from "./Pending";
import { planStatus } from "./plan";
import { ProposalCard } from "./ProposalCard";
import { SHOW_STEP, type ShownStep, ShownStepContext } from "./showStep";
import { GroupRow, HOVER_TITLE, Marker, NowCard, SayRow, StepRow, Story } from "./Story";
import { canContinue, type Item, type ModelStatus, type Turn } from "./transcript";

/** An answer asked with Express on says so, under its question. */
function ExpressTag() {
  return (
    <p className="-mt-2 flex">
      <Chip title={EXPRESS_TITLE}>Express</Chip>
    </p>
  );
}

export function TurnView({
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
  const shown = useShownStep(storyRows);
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
  // A step the chat was asked to show that sits in the folded part unfolds the story.
  useEffect(() => {
    const holds = (row: Row) =>
      row.type === "step" ? row.step.key === shown?.key : row.type === "group" && row.steps.some((step) => step.key === shown?.key);
    if (shown && folded && !shownRows.some(holds)) setAllSteps(true);
  }, [shown, folded, shownRows]);
  const story = (
    <ShownStepContext value={shown}>
      <Story rows={shownRows} renderRow={renderRow} />
    </ShownStepContext>
  );
  // Completed with an answer: the answer leads, the work behind it folds away.
  // A failed or stopped turn keeps its whole story in view.
  const finished = Boolean(answer) && turn.status === "completed" && !live;
  return (
    <article className={clsx("flex flex-col", compact ? "gap-3" : "gap-5")} data-question-seq={turn.seq}>
      {turn.userText && <Question text={turn.userText} continues={turn.continues} />}
      {turn.express && <ExpressTag />}
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
      {turn.items.map((item) =>
        item.kind === "kb_suggestion" ? <KbSuggestionCard key={item.id} suggestion={item} conversationId={conversationId} /> : null,
      )}
      {finished && <MadeHere items={turn.items} conversationId={conversationId} />}
      {finished && (
        <HowItWasMade rows={storyRows} shown={shown} express={turn.express}>
          <ShownStepContext value={shown}>
            <Story rows={storyRows} renderRow={renderRow} />
          </ShownStepContext>
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
function HowItWasMade({
  rows,
  shown,
  express = false,
  children,
}: {
  rows: Row[];
  shown: ShownStep | null;
  /** Asked with Express on. */
  express?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // Asked to show one of its steps (from the Code tab): open onto it.
  useEffect(() => {
    if (shown) setOpen(true);
  }, [shown]);
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
            {express && <Chip title={EXPRESS_TITLE}>Express</Chip>}
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
 * The rows a docked chat shows of a story in progress: the latest few, and,
 * wherever they are, every approval, every notice (DataLab's own notices carry
 * no tone, and some say something failed), and every step that errored or
 * needs a look (a join with no shared participant ID, say).
 */
export function latestRows(rows: Row[]): { shown: Row[]; folded: number } {
  if (rows.length <= LATEST_ROWS + 1) return { shown: rows, folded: 0 };
  const cut = rows.length - LATEST_ROWS;
  const kept = (row: Row, i: number) =>
    i >= cut ||
    row.type === "approval" ||
    row.type === "notice" ||
    (row.type === "step" && (row.step.tone === "error" || row.step.tone === "attn"));
  const shown = rows.filter(kept);
  return { shown, folded: rows.length - shown.length };
}

/** A step of this turn the chat was asked to show (showStep.ts), or null.
 *  Its `key` says which (activity.ts: "cmd-<id>"), so a view that folds steps
 *  can unfold the one holding it. */
export function useShownStep(rows: Row[]): ShownStep | null {
  const keys = rows
    .flatMap((row) => (row.type === "step" ? [row.step.key] : row.type === "group" ? row.steps.map((step) => step.key) : []))
    .join("\u0000");
  const [shown, setShown] = useState<ShownStep | null>(null);
  useEffect(() => {
    const mine = new Set(keys.split("\u0000"));
    const show = (event: Event) => {
      const key = String((event as CustomEvent<{ key?: string }>).detail?.key ?? "");
      if (key && mine.has(key)) setShown((before) => ({ key, n: (before?.n ?? 0) + 1 }));
    };
    window.addEventListener(SHOW_STEP, show);
    return () => window.removeEventListener(SHOW_STEP, show);
  }, [keys]);
  return shown;
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
