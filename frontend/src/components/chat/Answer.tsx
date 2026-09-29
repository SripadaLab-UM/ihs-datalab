// A turn's answer, set apart from the work behind it, with the checks on it;
// and the agent's rigor review of that work.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { use, useId, useMemo, useState } from "react";

import { api } from "@/api/client";
import { Button, Chip, Icon, InfoTip } from "@/components/ui";
import { OpenFileContext, workspaceFile } from "@/lib/files";

import { CompactContext } from "./assistants";
import type { CheckLine } from "./checks";
import { Markdown } from "./Markdown";
import { ShowQueryContext } from "./provenance";
import { HOVER_TITLE, Marker } from "./Story";
import type { Item, Turn } from "./transcript";

/** The first line of Markdown text, without its markup. */
function firstLine(text: string): string {
  const line = text.split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  return line.replace(/^#+\s*|^[-*]\s+|^\d+\.\s+/, "").replace(/\*\*|`/g, "");
}
/** The answer, set apart from the work behind it. */
export function AnswerCard({
  answer,
  trace,
  streaming,
  turn,
  checks,
}: {
  answer: string;
  trace: Turn["trace"];
  streaming: boolean;
  turn: Turn;
  checks: CheckLine[];
}) {
  const openFile = use(OpenFileContext);
  const openQuery = use(ShowQueryContext);
  const compact = use(CompactContext);
  // Where each number appears, once the turn's provenance has arrived.
  const numbers = useMemo(() => {
    if (!turn.provenance || streaming) return undefined;
    const commands = new Map(turn.items.flatMap((i) => (i.kind === "command" ? [[i.id, i.command] as const] : [])));
    return {
      sources: new Map(turn.provenance.numbers.map((n) => [n.text, n.sources])),
      links: {
        commandText: (id: string) => commands.get(id),
        // Only where there's a Queries tab to show it in; elsewhere, just its id.
        openQuery: openQuery ?? undefined,
        openFile: openFile
          ? (path: string) => {
              const file = workspaceFile(`/work/${path}`);
              if (file) openFile(file);
            }
          : undefined,
      },
    };
  }, [turn, streaming, openFile, openQuery]);
  return (
    <section
      data-tour="answer"
      className={clsx(
        "rounded-[4px] border border-line border-t-2 border-t-ink bg-surface",
        compact ? "min-w-0 px-4 pt-3 pb-4 [&_.prose-datalab]:text-[1rem]" : "mt-2 px-6 pt-4 pb-5 [&_.prose-datalab]:text-[1.2rem]",
      )}
    >
      <h3 className="mb-3 flex items-center gap-2 font-sans text-[12px] font-semibold tracking-[0.08em] text-ink uppercase">
        {streaming ? "Writing the answer…" : "Answer"}
      </h3>
      <Markdown text={answer} numbers={numbers} answer />
      {numbers && turn.provenance && turn.provenance.more_numbers > 0 && (
        <p className="mt-3 font-sans text-[12.5px] text-muted">
          {turn.provenance.more_numbers} more number{turn.provenance.more_numbers === 1 ? "" : "s"} in this answer
          weren't checked for where {turn.provenance.more_numbers === 1 ? "it appears" : "they appear"}.
        </p>
      )}
      {!streaming && checks.length > 0 && <Checks lines={checks} untraced={trace?.untraced ?? []} folded={compact} />}
    </section>
  );
}

/**
 * The checks on the answer, each saying whose check it is and whether a
 * problem remains: DataLab's own number check first, then the agent's rigor
 * review, why they differ when they seem to, and the steps that failed.
 */
function Checks({ lines, untraced, folded = false }: { lines: CheckLine[]; untraced: string[]; folded?: boolean }) {
  // Docked, the checks fold into their heading, which says whether any needs a look.
  const [open, setOpen] = useState(!folded);
  const listId = useId();
  const worst = lines.some((l) => l.tone === "bad") ? "bad" : lines.some((l) => l.tone === "attn") ? "attn" : null;
  return (
    <div data-testid="answer-checks" className={clsx("flex flex-col gap-1.5 border-t border-line font-sans text-[13px] leading-snug", folded ? "mt-4 pt-2" : "mt-6 pt-3")}>
      <h4 className="dl-label flex items-center gap-1.5">
        {folded ? (
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls={listId} className="group flex items-center gap-1.5 uppercase">
            <Icon name="chevron" size={11} className={clsx("transition-transform", open && "rotate-90")} />
            <span className={HOVER_TITLE}>Checks on this answer</span>
            {!open && worst && <Chip tone={worst}>{worst === "bad" ? "a problem" : "needs a look"}</Chip>}
          </button>
        ) : (
          "Checks on this answer"
        )}{" "}
        <InfoTip term="trace-and-provenance" />
      </h4>
      {open && (
        <ul id={listId} className="flex flex-col gap-1.5">
          {lines.map((line) => (
            <li
              key={line.key}
              className={clsx(
                "flex items-baseline gap-2",
                line.key === "differ" ? "pl-[18px] text-muted italic" : "text-ink",
              )}
            >
              {line.key !== "differ" &&
                (line.tone === "plain" ? (
                  <span aria-hidden className="mx-[3.5px] inline-block h-[5px] w-[5px] shrink-0 -translate-y-[1px] rounded-full bg-muted" />
                ) : (
                  <Icon
                    name={line.tone === "good" ? "check" : "alert"}
                    size={12}
                    className={clsx(
                      "shrink-0 translate-y-[1px]",
                      line.tone === "good" && "text-data",
                      line.tone === "attn" && "text-attn",
                      line.tone === "bad" && "text-danger",
                    )}
                  />
                ))}
              <span className="min-w-0">
                {line.text}
                {line.key === "trace" && untraced.length > 0 && (
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    {untraced.slice(0, 8).map((n) => (
                      <Chip key={n} tone="attn" title="Not in this turn's query results, command output, or data files: check it">
                        {n}
                      </Chip>
                    ))}
                    {untraced.length > 8 && <Chip tone="attn">+{untraced.length - 8}</Chip>}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ReviewBox({
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
          <span className="block font-sans text-[14.5px] text-ink">
            <span className={HOVER_TITLE}>Rigor review</span>
          </span>
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
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 enabled:hover:text-danger"
          >
            Stop the review
          </button>
        )}
        {unfinished && last && (
          <button
            type="button"
            onClick={() => again.mutate()}
            disabled={running || again.isPending || again.isSuccess}
            className="shrink-0 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 enabled:hover:decoration-ink"
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
