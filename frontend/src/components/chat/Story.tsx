import clsx from "clsx";
import { type ReactNode, use, useState } from "react";

import { Button, Chip, Icon } from "@/components/ui";
import { OpenFileContext, workspaceFile } from "@/lib/files";

import type { Detail, Row, Step } from "./activity";
import { shortTable, unwrapShell as unwrap } from "./activity";
import { Markdown } from "./Markdown";

/**
 * The agent's work, told as a story: a timeline of plain sentences, each
 * opening onto what the agent actually read or ran. The live step breathes.
 */
export function Story({ rows, renderRow }: { rows: Row[]; renderRow: (row: Row) => ReactNode }) {
  if (rows.length === 0) return null;
  return (
    <ol className="relative flex flex-col gap-0.5 pl-9 before:absolute before:top-3 before:bottom-3 before:left-[13px] before:w-0.5 before:rounded before:bg-line">
      {rows.map((row) => (
        <li key={rowKey(row)} className="dl-in">
          {renderRow(row)}
        </li>
      ))}
    </ol>
  );
}

function rowKey(row: Row): string {
  switch (row.type) {
    case "step":
      return row.step.key;
    case "say":
    case "notice":
    case "group":
      return row.key;
    case "approval":
      return `approval-${row.approval.id}`;
    case "review":
      return `review-${row.index}`;
  }
}

/** A disc on the timeline. */
export function Marker({ icon, tone }: { icon: Parameters<typeof Icon>[0]["name"]; tone: Step["tone"] | "you" }) {
  return (
    <span
      className={clsx(
        "absolute top-2 -left-9 grid size-7 place-items-center rounded-full border-2 bg-surface",
        tone === "done" && "border-accent/70 text-accent",
        tone === "now" && "dl-breathe border-accent bg-accent text-accent-ink",
        tone === "attn" && "border-attn text-attn",
        tone === "error" && "border-danger text-danger",
        tone === "you" && "border-you text-you",
      )}
    >
      <Icon name={icon} size={14} />
    </span>
  );
}

/** A button when there is something to open; otherwise plain text, so it isn't a dead stop for the keyboard. */
function Pressable({
  open,
  onToggle,
  className,
  children,
}: {
  open: boolean | null;
  onToggle: () => void;
  className: string;
  children: ReactNode;
}) {
  if (open === null) return <div className={className}>{children}</div>;
  return (
    <button type="button" onClick={onToggle} aria-expanded={open} className={className}>
      {children}
    </button>
  );
}

export function StepRow({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const canOpen = step.detail !== null;
  return (
    <div className="relative">
      <Marker icon={step.icon} tone={step.tone} />
      <Pressable
        open={canOpen ? open : null}
        onToggle={() => setOpen(!open)}
        className={clsx(
          "group flex w-full flex-col items-start gap-1 rounded-xl px-3 py-2 text-left",
          canOpen ? "cursor-pointer hover:bg-surface" : "cursor-default",
          open && "bg-surface",
        )}
      >
        <span className="flex w-full items-baseline gap-2">
          <span className={clsx("text-[15px] leading-snug", step.tone === "now" ? "font-semibold text-ink" : "text-ink")}>
            {step.title}
          </span>
          {canOpen && (
            <Icon
              name="chevron"
              size={14}
              className={clsx("ml-auto shrink-0 self-center text-faint transition-transform group-hover:text-muted", open && "rotate-90")}
            />
          )}
        </span>
        {step.chips.length > 0 && (
          <span className="flex flex-wrap gap-1.5">
            {step.chips.map((chip, i) => (
              <Chip key={i} tone={chip.tone}>
                {chip.text}
              </Chip>
            ))}
          </span>
        )}
      </Pressable>
      {open && step.detail && (
        <div className="mx-3 mt-1 mb-3 rounded-2xl border border-line bg-surface p-4">
          <DetailView detail={step.detail} />
        </div>
      )}
    </div>
  );
}

/** Several steps of one kind, folded into a row that opens onto each. */
export function GroupRow({ row }: { row: Extract<Row, { type: "group" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Marker icon={row.icon} tone="done" />
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className={clsx("group flex w-full flex-col items-start gap-1 rounded-xl px-3 py-2 text-left hover:bg-surface", open && "bg-surface")}
      >
        <span className="flex w-full items-baseline gap-2">
          <span className="text-[15px] leading-snug text-ink">{row.title}</span>
          <Icon name="chevron" size={14} className={clsx("ml-auto shrink-0 self-center text-faint transition-transform group-hover:text-muted", open && "rotate-90")} />
        </span>
        {row.chips.length > 0 && (
          <span className="flex flex-wrap gap-1.5">
            {row.chips.map((chip, i) => (
              <Chip key={i} tone={chip.tone}>
                {chip.text}
              </Chip>
            ))}
          </span>
        )}
      </button>
      {open && (
        <ol className="mt-1 mb-2 ml-3 flex flex-col border-l-2 border-line pl-2">
          {row.steps.map((step) => (
            <li key={step.key}>
              <InnerStep step={step} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** A step inside a group: the same, without its own disc. */
function InnerStep({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <Pressable
        open={step.detail ? open : null}
        onToggle={() => setOpen(!open)}
        className={clsx("flex w-full items-baseline gap-2 rounded-lg px-2 py-1.5 text-left text-[14px]", step.detail && "hover:bg-sunken")}
      >
        <span className="flex-1">{step.title}</span>
        {step.chips.slice(0, 2).map((chip, i) => (
          <Chip key={i} tone={chip.tone}>
            {chip.text}
          </Chip>
        ))}
      </Pressable>
      {open && step.detail && (
        <div className="mx-2 mt-1 mb-2 rounded-2xl border border-line bg-surface p-4">
          <DetailView detail={step.detail} />
        </div>
      )}
    </div>
  );
}

/** The agent's own words while it works. */
export function SayRow({ text }: { text: string }) {
  return (
    <div className="relative">
      <span className="absolute top-3.5 -left-[27px] size-2.5 rounded-full border-2 border-line bg-canvas" aria-hidden="true" />
      <div className="max-w-[62ch] px-3 py-1.5 text-[15px] leading-relaxed text-muted">
        <Markdown text={text} />
      </div>
    </div>
  );
}

/** What the agent is doing this moment. */
export function NowCard({
  line,
  waiting,
  onStop,
  stopping,
}: {
  line: string;
  waiting?: string;
  onStop: () => void;
  stopping: boolean;
}) {
  return (
    <div
      className={clsx("flex items-center gap-3 rounded-2xl border bg-surface px-4 py-3", waiting ? "border-you/50" : "border-accent/50")}
      role="status"
      aria-live="polite"
    >
      <span className={clsx("grid size-7 shrink-0 place-items-center rounded-full", waiting ? "bg-you-soft text-you" : "dl-breathe bg-accent text-accent-ink")}>
        <Icon name={waiting ? "check" : "spark"} size={14} />
      </span>
      <span className="min-w-0 flex-1">
        <span className={clsx("block text-[11px] font-semibold uppercase tracking-[0.08em]", waiting ? "text-you" : "text-accent")}>
          {waiting ? "Waiting for you" : "Working now"}
        </span>
        <span className="block truncate text-[15px] text-ink">{waiting ?? line}</span>
      </span>
      {!waiting && (
        <span className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-line sm:block" aria-hidden="true">
          <i className="dl-slide block h-full w-2/5 rounded-full bg-accent" />
        </span>
      )}
      <Button variant="ghost" onClick={onStop} disabled={stopping} className="text-danger">
        <Icon name="stop" size={14} /> Stop
      </Button>
    </div>
  );
}

function Caption({ children }: { children: ReactNode }) {
  return <p className="mb-2 text-[13px] text-muted">{children}</p>;
}

function Mono({ children }: { children: ReactNode }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-xl bg-sunken px-3 py-2.5 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink">
      {children}
    </pre>
  );
}

function OpenButton({ path, label }: { path: string; label: string }) {
  const openFile = use(OpenFileContext);
  const file = workspaceFile(path);
  if (!file || !openFile) return null;
  return (
    <Button onClick={() => openFile(file)} className="text-[13px]">
      <Icon name="open" size={14} /> {label}
    </Button>
  );
}

export function DetailView({ detail }: { detail: Detail }) {
  switch (detail.kind) {
    case "guide":
      return (
        <>
          <Caption>
            <Icon name="book" size={13} className="mr-1 inline align-[-2px]" />
            What the agent read, from the lab's guide on <b className="text-ink">{detail.name}</b>
          </Caption>
          <div className="max-h-96 overflow-auto rounded-xl bg-canvas px-4 py-3">
            <Markdown text={stripFrontMatter(detail.text) || "(The guide came back empty.)"} />
          </div>
        </>
      );
    case "tables":
      return (
        <>
          <Caption>
            Searched the catalog for <b className="text-ink">“{detail.searched}”</b>. Names and descriptions only: no data
            was read.
          </Caption>
          {detail.tables.length === 0 ? (
            <p className="text-sm text-muted">Nothing matched.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-line">
              {detail.tables.map((t, i) => (
                <li key={`${t.table}-${i}`} className="flex flex-col gap-1 py-2">
                  <span className="font-mono text-[13px] font-medium text-ink">{shortTable(t.table)}</span>
                  {t.comment && <span className="text-[13px] text-muted">{t.comment}</span>}
                  {t.columns.length > 0 && (
                    <span className="flex flex-wrap gap-1">
                      {t.columns.map((c, j) => (
                        <Chip key={`${c}-${j}`}>{c}</Chip>
                      ))}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      );
    case "columns":
      return <Columns detail={detail} />;
    case "join":
      return (
        <>
          <Caption>
            How <b className="text-ink">{shortTable(detail.tables[0])}</b> and{" "}
            <b className="text-ink">{shortTable(detail.tables[1])}</b> line up.
          </Caption>
          {detail.shared.length === 0 ? (
            <p className="text-sm text-muted">They share no columns.</p>
          ) : (
            <table className="w-full text-[13px]">
              <tbody>
                {detail.shared.map((k, i) => (
                  <tr key={`${k.column}-${i}`} className="border-b border-line last:border-0">
                    <td className="py-1.5 pr-3 font-mono font-medium">{k.column}</td>
                    <td className="py-1.5 pr-3 text-muted">{k.role === "participant" ? "the person" : k.role === "date" ? "a date" : k.role === "audit" ? "when the row was written" : "other"}</td>
                    <td className="py-1.5 text-attn">{k.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {detail.notes.map((note, i) => (
            <p key={i} className="mt-2 flex gap-2 rounded-xl bg-attn-soft px-3 py-2 text-[13.5px] text-ink">
              <Icon name="alert" size={15} className="mt-0.5 shrink-0 text-attn" />
              {note}
            </p>
          ))}
        </>
      );
    case "query":
      return (
        <>
          <Caption>
            {detail.error
              ? "The database refused this query. Nothing was read."
              : `A read-only query${detail.rows === null ? "" : `: ${detail.rows.toLocaleString()} row${detail.rows === 1 ? "" : "s"}`}. It's in the Data accessed log.`}
          </Caption>
          <Mono>{detail.sql || "(no SQL recorded)"}</Mono>
          {detail.error && <p className="mt-2 text-[13px] text-danger">{detail.error}</p>}
          {detail.columns.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <span className="mr-1 text-[13px] text-muted">Columns:</span>
              {detail.columns.slice(0, 16).map((c, j) => (
                <Chip key={`${c}-${j}`}>{c}</Chip>
              ))}
              {detail.columns.length > 16 && <Chip>+{detail.columns.length - 16}</Chip>}
            </div>
          )}
          {detail.warnings.map((w, i) => (
            <p key={i} className="mt-2 text-[13px] text-attn">{w}</p>
          ))}
          {detail.file && (
            <div className="mt-3">
              <OpenButton path={detail.file} label="Open the result" />
            </div>
          )}
        </>
      );
    case "command":
      return (
        <>
          <Caption>
            In this conversation's own container, which has no internet.
            {detail.exitCode !== null && detail.exitCode !== 0 && <span className="text-danger"> It stopped with an error.</span>}
          </Caption>
          <Mono>
            <span className="text-faint select-none">$ </span>
            {unwrap(detail.command)}
          </Mono>
          {detail.output && (
            <div className="mt-2">
              <Mono>{detail.output}</Mono>
            </div>
          )}
        </>
      );
    case "files":
      return (
        <ul className="flex flex-col gap-2">
          {detail.paths.map((path) => (
            <li key={path} className="flex items-center gap-3">
              <Icon name="file" size={15} className="text-muted" />
              <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{path.replace(/^\/work\//, "")}</span>
              <OpenButton path={path} label="Open" />
            </li>
          ))}
        </ul>
      );
  }
}

function Columns({ detail }: { detail: Extract<Detail, { kind: "columns" }> }) {
  const [all, setAll] = useState(false);
  const shown = all ? detail.columns : detail.columns.slice(0, 12);
  return (
    <>
      <Caption>
        <b className="text-ink">{shortTable(detail.table)}</b> · {detail.count.toLocaleString()} columns
        {detail.alsoIn.length > 0 && <> · also in {detail.alsoIn.map((c) => c.replace("IHS_", "")).join(", ")}</>}
        {detail.comment && <span className="block">{detail.comment}</span>}
      </Caption>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <tbody>
            {shown.map((c, i) => (
              <tr key={`${c.name}-${i}`} className="border-b border-line last:border-0">
                <td className="py-1.5 pr-3 font-mono font-medium whitespace-nowrap">{c.name}</td>
                <td className="py-1.5 pr-3 font-mono whitespace-nowrap text-muted">{c.type}</td>
                <td className="py-1.5 text-muted">{c.comment}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail.columns.length > 12 && (
        <Button variant="ghost" className="mt-1 text-[13px]" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${detail.columns.length}`}
        </Button>
      )}
    </>
  );
}

/** Guides start with front matter (name, description) that isn't for reading. */
function stripFrontMatter(text: string): string {
  return text.replace(/^---\n[\s\S]*?\n---\n?/, "").trim();
}

