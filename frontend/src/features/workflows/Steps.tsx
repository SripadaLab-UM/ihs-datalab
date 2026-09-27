import clsx from "clsx";
import { type ReactNode, useState } from "react";

import type { RunStep } from "@/api/workflows";
import { Marker } from "@/components/chat/Story";
import { Chip } from "@/components/ui";

import { duration, plural, short, STEP_KIND, stepStatus } from "./words";

// Everything here comes from the run record: names, counts, checksums and
// the checks' own messages. Never rows. Messages a step's R code wrote are
// its own words, shown as text.

type Check = { id: string; status: string; observed?: unknown; expected?: unknown; message?: string };
type Message = { level: string; text: string };
type FileFacts = { file?: string; sha256?: string; rows?: number; columns?: unknown[]; bytes?: number; step?: string };

/** A run's steps, in order, each opening onto what it did. */
export function RunSteps({ steps }: { steps: RunStep[] }) {
  return (
    <ol aria-label="Steps" className="flex flex-col border-t border-line">
      {steps.map((step) => (
        <li key={step.step_id} className="border-b border-line">
          <StepItem step={step} />
        </li>
      ))}
    </ol>
  );
}

function StepItem({ step }: { step: RunStep }) {
  // A failed step opens by itself (also when it fails while you watch), until you close it.
  const [chosen, setOpen] = useState<boolean | null>(null);
  const open = chosen ?? step.status === "failed";
  const status = stepStatus(step.status);
  const checks = checksOf(step);
  const failedChecks = checks.filter((c) => c.status !== "pass").length;
  const hasDetail = step.status !== "pending" && step.status !== "skipped";
  const tone = step.status === "running" ? "now" : step.status === "failed" ? "error" : "done";
  return (
    <div>
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setOpen(!open)}
        aria-expanded={hasDetail ? open : undefined}
        className="group flex w-full items-start gap-3 py-2.5 text-left disabled:cursor-default"
      >
        <Marker tone={tone} open={hasDetail ? open : null} />
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 font-sans text-[14px]">
            <span className={clsx("font-mono text-[13px]", step.status === "failed" ? "text-danger" : "text-ink")}>
              {step.step_id}
            </span>
            <span className="text-muted">{STEP_KIND[step.kind]}</span>
            <Chip tone={status.tone}>{status.text}</Chip>
            {checks.length > 0 && (
              <Chip tone={failedChecks ? "bad" : "good"}>
                {failedChecks
                  ? `${failedChecks} of ${plural(checks.length, "check")} failed`
                  : `${plural(checks.length, "check")} passed`}
              </Chip>
            )}
            {step.elapsed_ms !== null && <span className="text-[12px] text-faint tabular">{duration(step.elapsed_ms)}</span>}
          </span>
          {step.message && (
            <span className={clsx("font-sans text-[13px]", step.status === "failed" ? "text-danger" : "text-muted")}>
              {step.message}
            </span>
          )}
        </span>
      </button>
      {open && hasDetail && (
        <div className="flex flex-col gap-3 pb-4 pl-[19px]">
          <StepDetail step={step} checks={checks} />
        </div>
      )}
    </div>
  );
}

function checksOf(step: RunStep): Check[] {
  const checks = (step.result as { checks?: unknown } | null)?.checks;
  return Array.isArray(checks) ? (checks as Check[]) : [];
}

function StepDetail({ step, checks }: { step: RunStep; checks: Check[] }) {
  const result = (step.result ?? {}) as { counts?: Record<string, unknown>; messages?: Message[] };
  const counts = Object.entries(result.counts ?? {});
  const messages = Array.isArray(result.messages) ? result.messages : [];
  const inputs = Object.entries(step.inputs as Record<string, FileFacts>);
  const outputs = Object.entries(step.outputs as Record<string, FileFacts>);
  return (
    <>
      {step.sql_text && (
        <Detail label="SQL">
          <pre className="overflow-x-auto rounded-[3px] bg-sunken px-3 py-2 font-mono text-[12px] leading-relaxed whitespace-pre text-ink">
            {step.sql_text.replace(/\n+$/, "")}
          </pre>
          {step.binds && Object.keys(step.binds).length > 0 && (
            <p className="mt-1 font-mono text-[12px] text-muted">
              {Object.entries(step.binds)
                .map(([name, value]) => `:${name} = ${String(value)}`)
                .join(" · ")}
            </p>
          )}
        </Detail>
      )}
      {step.queries.length > 0 && (
        <Detail label="Extracted for the pipeline">
          <ul className="font-mono text-[12px] text-ink">
            {step.queries.map((q, i) => (
              <li key={i}>{String((q as { object?: unknown }).object ?? "")}</li>
            ))}
          </ul>
        </Detail>
      )}
      {checks.length > 0 && (
        <Detail label="Checks">
          <table className="w-full font-sans text-[12.5px]">
            <thead className="text-left text-faint">
              <tr>
                <th className="py-1 pr-3 font-normal">Check</th>
                <th className="py-1 pr-3 font-normal">Result</th>
                <th className="py-1 pr-3 font-normal">Found</th>
                <th className="py-1 pr-3 font-normal">Wanted</th>
                <th className="py-1 font-normal">Note</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((c, i) => (
                <tr key={`${c.id}-${i}`} className="border-t border-line align-baseline">
                  <td className="py-1 pr-3 font-mono text-[12px] text-ink">{c.id}</td>
                  <td className={clsx("py-1 pr-3", c.status === "pass" ? "text-data" : "text-danger")}>
                    {c.status === "pass" ? "passed" : "failed"}
                  </td>
                  <td className="py-1 pr-3 font-mono text-[12px] tabular">{shown(c.observed)}</td>
                  <td className="py-1 pr-3 font-mono text-[12px] tabular">{shown(c.expected)}</td>
                  <td className="py-1 text-muted">{c.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Detail>
      )}
      {counts.length > 0 && (
        <Detail label="Counts">
          <p className="flex flex-wrap gap-x-4 font-mono text-[12px] text-ink">
            {counts.map(([name, value]) => (
              <span key={name}>
                {name} <span className="tabular">{shown(value)}</span>
              </span>
            ))}
          </p>
        </Detail>
      )}
      {messages.length > 0 && (
        <Detail label="Messages">
          <ul className="flex flex-col gap-0.5 font-sans text-[12.5px]">
            {messages.map((m, i) => (
              <li key={i} className={m.level === "error" ? "text-danger" : m.level === "warning" ? "text-attn" : "text-ink"}>
                {m.text}
              </li>
            ))}
          </ul>
        </Detail>
      )}
      {inputs.length > 0 && (
        <Detail label="Read">
          <Files files={inputs} />
        </Detail>
      )}
      {outputs.length > 0 && (
        <Detail label="Wrote">
          <Files files={outputs} />
        </Detail>
      )}
      {(step.seed !== null || step.exit_code !== null) && (
        <p className="font-mono text-[12px] text-muted">
          {step.seed !== null && <>seed {step.seed}</>}
          {step.seed !== null && step.exit_code !== null && " · "}
          {step.exit_code !== null && <>exit code {step.exit_code}</>}
        </p>
      )}
    </>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="dl-label mb-1">{label}</p>
      {children}
    </div>
  );
}

function Files({ files }: { files: [string, FileFacts][] }) {
  return (
    <ul className="flex flex-col gap-0.5 font-mono text-[12px] text-muted">
      {files.map(([name, f]) => (
        <li key={name} className="flex flex-wrap gap-x-3">
          <span className="text-ink">{f.file ?? name}</span>
          {typeof f.rows === "number" && <span>{plural(f.rows, "row")}</span>}
          {Array.isArray(f.columns) && <span>{plural(f.columns.length, "column")}</span>}
          {f.sha256 && <span title={f.sha256}>sha256 {short(f.sha256)}</span>}
        </li>
      ))}
    </ul>
  );
}

function shown(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  return String(value);
}
