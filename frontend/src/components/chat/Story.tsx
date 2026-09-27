import clsx from "clsx";
import { type ReactNode, use, useState } from "react";

import { Button, Chip, Icon } from "@/components/ui";
import { OpenFileContext, workspaceFile } from "@/lib/files";

import type { Detail, Row, Step } from "./activity";
import { shortTable, unwrapShell as unwrap } from "./activity";
import { Markdown } from "./Markdown";

/**
 * The agent's work, told as a story: plain sentences on hairline rules, each
 * opening ("+") onto what the agent actually read or ran, with the agent's
 * own words between them as narration.
 */
export function Story({ rows, renderRow }: { rows: Row[]; renderRow: (row: Row) => ReactNode }) {
  if (rows.length === 0) return null;
  const ruled = (row: Row | undefined) => row?.type === "step" || row?.type === "group";
  return (
    <ol className="flex flex-col">
      {rows.map((row, i) => (
        <li
          key={rowKey(row)}
          className={clsx(
            "dl-in",
            ruled(row) && "border-b border-line",
            ruled(row) && !ruled(rows[i - 1]) && "mt-1 border-t",
            !ruled(row) && i > 0 && "mt-3",
          )}
        >
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
    case "proposal":
      return `proposal-${row.proposal.id}`;
  }
}

/** The mark at the start of a row: "+" to open it, or what state it's in. */
export function Marker({ tone, open }: { tone: Step["tone"] | "you"; open: boolean | null }) {
  if (tone === "now") return <span aria-hidden className="dl-breathe mt-[7px] size-[7px] shrink-0 rounded-full bg-ink" />;
  if (open === null) {
    return <span aria-hidden className={clsx("mt-[8px] size-[5px] shrink-0 rounded-full", tone === "error" ? "bg-danger" : "bg-line")} />;
  }
  return (
    <span
      aria-hidden
      className={clsx(
        "w-[7px] shrink-0 font-sans text-[15px] leading-[1.4] transition-transform",
        open && "rotate-45",
        tone === "error" ? "text-danger" : tone === "attn" ? "text-attn" : "text-faint group-hover:text-ink",
      )}
    >
      +
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

function Chips({ chips }: { chips: Step["chips"] }) {
  if (chips.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1.5">
      {chips.map((chip, i) => (
        <Chip key={i} tone={chip.tone}>
          {chip.text}
        </Chip>
      ))}
    </span>
  );
}

function Title({ step }: { step: Pick<Step, "icon" | "title" | "tone"> }) {
  return (
    <span
      className={clsx(
        "flex items-baseline gap-2 font-sans text-[14.5px] leading-snug",
        step.tone === "now" && "font-medium",
        step.tone === "error" ? "text-danger" : step.tone === "attn" ? "text-attn" : "text-ink",
      )}
    >
      <Icon name={step.icon} size={13} className="shrink-0 translate-y-[1.5px] text-faint" />
      {step.title}
    </span>
  );
}

export function StepRow({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const can = step.detail !== null;
  return (
    <div>
      <Pressable
        open={can ? open : null}
        onToggle={() => setOpen(!open)}
        className={clsx("group flex w-full items-start gap-3 py-2.5 text-left", can ? "cursor-pointer" : "cursor-default")}
      >
        <Marker tone={step.tone} open={can ? open : null} />
        <span className="flex min-w-0 flex-1 flex-col gap-1.5">
          <Title step={step} />
          <Chips chips={step.chips} />
        </span>
      </Pressable>
      {open && step.detail && (
        <div className="pb-5 pl-[19px]">
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
    <div>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="group flex w-full items-start gap-3 py-2.5 text-left">
        <Marker tone="done" open={open} />
        <span className="flex min-w-0 flex-1 flex-col gap-1.5">
          <Title step={{ icon: row.icon, title: row.title, tone: "done" }} />
          <Chips chips={row.chips} />
        </span>
      </button>
      {open && (
        <ol className="mb-3 ml-[19px] flex flex-col border-t border-line">
          {row.steps.map((step) => (
            <li key={step.key} className="border-b border-line last:border-0">
              <InnerStep step={step} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** A step inside a group: the same, a little quieter. */
function InnerStep({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <Pressable
        open={step.detail ? open : null}
        onToggle={() => setOpen(!open)}
        className="group flex w-full items-start gap-3 py-2 text-left"
      >
        <Marker tone={step.tone} open={step.detail ? open : null} />
        <span className="flex-1 font-sans text-[14px] text-ink">{step.title}</span>
        <Chips chips={step.chips.slice(0, 2)} />
      </Pressable>
      {open && step.detail && (
        <div className="pb-4 pl-[19px]">
          <DetailView detail={step.detail} />
        </div>
      )}
    </div>
  );
}

/** The agent's own words while it works: narration, set as reading text. */
export function SayRow({ text }: { text: string }) {
  return (
    <div className="max-w-[72ch] py-1 text-muted [&_.prose-datalab]:text-[1.06rem]">
      <Markdown text={text} />
    </div>
  );
}

/**
 * What the agent is doing this moment, or what it's waiting for you to do.
 * A block of its own, set apart from the story, with Stop always beside it.
 */
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
      className={clsx(
        "flex items-start gap-3 rounded-[4px] border px-4 py-3",
        waiting ? "border-you bg-you-soft" : "border-line bg-field",
      )}
      role="status"
      aria-live="polite"
    >
      <span
        aria-hidden
        className={clsx("mt-[7px] size-2 shrink-0 rounded-full", waiting ? "bg-you" : "dl-breathe bg-ink")}
      />
      <span className="min-w-0 flex-1">
        <span className={clsx("block font-sans text-[12px] font-semibold tracking-[0.08em] uppercase", waiting ? "text-you" : "text-ink")}>
          {waiting ? "Waiting for you" : "Working"}
        </span>
        <span className="mt-0.5 line-clamp-2 block font-sans text-[15px] leading-snug text-ink">{waiting ?? line}</span>
      </span>
      <Button variant="danger" onClick={onStop} disabled={stopping} className="shrink-0 self-center px-2.5 py-1 text-[13px]">
        <Icon name="stop" size={12} /> Stop
      </Button>
    </div>
  );
}

function Caption({ children }: { children: ReactNode }) {
  return <p className="mb-2.5 font-sans text-[12.5px] text-muted">{children}</p>;
}

function Mono({ children }: { children: ReactNode }) {
  return (
    <pre className="max-h-72 overflow-auto bg-sunken px-3.5 py-3 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink">
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
          <div className="max-h-[28rem] overflow-auto border-l border-ink pl-5 [&_.prose-datalab]:text-[1.03rem]">
            <Markdown text={stripFrontMatter(detail.text) || "(The guide came back empty.)"} />
          </div>
          <p className="mt-2.5 font-sans text-[12px] text-faint">
            What the agent read, from the lab's guide on <b className="font-medium text-muted">{detail.name}</b>
          </p>
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
            <p key={i} className="mt-3 border-l border-attn pl-3 font-serif text-[15.5px] text-ink">
              <span className="dl-label mr-2 !text-attn">Heads-up</span>
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
              : `A read-only query${detail.rows === null ? "" : `: ${detail.rows.toLocaleString()} row${detail.rows === 1 ? "" : "s"}`}. It's listed under Queries.`}
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
            In this conversation's own sealed container.
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

