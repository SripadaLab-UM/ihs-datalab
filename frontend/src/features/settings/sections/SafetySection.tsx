import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useId, useState } from "react";

import { api, type CheckResult, type SafetyReport } from "@/api/client";
import { Button, Icon } from "@/components/ui";

export interface SafetySummary {
  /** "Every check passed", "2 problems", "Not run yet". */
  text: string;
  tone: "good" | "attn" | "bad" | "none";
  /** When the last check finished, if there was one. */
  checkedAt: string | null;
}

/** The last report in a line: how it went and when. Shared with the section list's mark. */
export function safetySummary(report: SafetyReport | null | undefined): SafetySummary {
  if (!report) return { text: "Not run yet", tone: "none", checkedAt: null };
  const failed = report.results.filter((r) => r.status === "fail").length;
  const unverified = report.results.filter((r) => r.status === "skip" && r.required).length;
  const checkedAt = report.finished_at;
  if (!report.passed) {
    const problems = failed + unverified;
    if (problems === 0) return { text: "Didn't pass", tone: "bad", checkedAt };
    return { text: `${problems} problem${problems === 1 ? "" : "s"}`, tone: "bad", checkedAt };
  }
  if (unverified) {
    return { text: `${unverified} check${unverified === 1 ? "" : "s"} couldn't be verified`, tone: "attn", checkedAt };
  }
  return { text: "Every check passed", tone: "good", checkedAt };
}

/**
 * The Safety check: live tests of DataLab's safety promises. How the last one
 * went sits in a line at the top; the full report is under Details.
 */
export function SafetySection() {
  const queryClient = useQueryClient();
  const last = useQuery({ queryKey: ["safety"], queryFn: api.lastSafetyReport });
  const [open, setOpen] = useState(false);
  const details = useId();
  const run = useMutation({
    mutationFn: api.runSafetyCheck,
    onSuccess: (report) => {
      queryClient.setQueryData(["safety"], report);
      // Something needs looking at: open the report.
      if (!report.passed) setOpen(true);
    },
  });
  const report = last.data;
  const summary = safetySummary(report);

  return (
    <section className="border-t border-ink pt-6">
      <div className="flex items-start justify-between gap-4">
        <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line text-data">
          <Icon name="shield" size={20} />
        </span>
        <div className="flex-1">
          <h2 className="font-serif text-[28px] leading-tight">Safety check</h2>
          <p className="mt-1 text-sm text-muted">
            Live tests of DataLab's safety promises: it starts sealed test sessions and tries to break out of them.
          </p>
        </div>
        <Button variant="primary" className="shrink-0" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "Checking… (about 20 s)" : "Run safety check"}
        </Button>
      </div>
      <p
        className={clsx(
          "mt-5 flex flex-wrap items-center gap-x-2 gap-y-1 border-l-2 pl-3 text-[15px]",
          summary.tone === "good" && "border-data",
          summary.tone === "attn" && "border-research",
          summary.tone === "bad" && "border-danger",
          summary.tone === "none" && "border-line",
        )}
        role="status"
        aria-label="Safety check summary"
      >
        <span
          className={clsx(
            "inline-flex items-center gap-1.5 font-medium",
            summary.tone === "good" && "text-data",
            summary.tone === "attn" && "text-research",
            summary.tone === "bad" && "text-danger",
            summary.tone === "none" && "text-muted",
          )}
        >
          {summary.tone !== "none" && (
            <Icon name={summary.tone === "good" ? "check" : summary.tone === "bad" ? "close" : "alert"} size={14} />
          )}
          {summary.text}
        </span>
        {summary.checkedAt && (
          <span className="text-muted">
            · checked <time dateTime={summary.checkedAt}>{new Date(summary.checkedAt).toLocaleString()}</time>
          </span>
        )}
      </p>
      {run.error && <p className="mt-3 text-sm text-danger">{run.error.message}</p>}
      {report && (
        <div className="mt-4">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={details}
            onClick={() => setOpen(!open)}
            className="inline-flex items-center gap-1.5 font-sans text-[13.5px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
          >
            <Icon name="chevron" size={13} className={clsx("transition-transform", open && "rotate-90")} />
            Details
          </button>
          <div id={details} hidden={!open}>
            {open && <Report report={report} />}
          </div>
        </div>
      )}
    </section>
  );
}

function Report({ report }: { report: SafetyReport }) {
  const byPromise = new Map<string, CheckResult[]>();
  for (const result of report.results) {
    byPromise.set(result.promise, [...(byPromise.get(result.promise) ?? []), result]);
  }
  const failed = report.results.filter((r) => r.status === "fail").length;
  const unverified = report.results.filter((r) => r.status === "skip" && r.required).length;
  return (
    <div className="mt-4">
      <div
        className={clsx(
          "flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium",
          !report.passed ? "bg-danger-soft text-danger" : unverified ? "bg-research-soft text-research" : "bg-data-soft text-data",
        )}
      >
        <Icon name={!report.passed ? "close" : unverified ? "alert" : "check"} />
        {!report.passed
          ? `${failed} check${failed > 1 ? "s" : ""} failed`
          : unverified
            ? `No failures, but ${unverified} check${unverified > 1 ? "s" : ""} couldn't be verified`
            : "Every check passed"}
        <span className="ml-auto font-normal text-muted">{new Date(report.finished_at).toLocaleString()}</span>
      </div>
      {[...byPromise].map(([promise, results]) => (
        <div key={promise} className="mt-5">
          <h3 className="dl-label">{promise}</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {results.map((r) => (
              <li key={r.id} className="flex gap-3 rounded-lg bg-canvas px-3 py-2 text-sm">
                <span
                  className={clsx(
                    "mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                    r.status === "pass" && "bg-data-soft text-data",
                    r.status === "fail" && "bg-danger-soft text-danger",
                    r.status === "skip" && (r.required ? "bg-research-soft text-research" : "bg-sunken text-muted"),
                  )}
                  role="img"
                  aria-label={r.status}
                >
                  <Icon name={r.status === "pass" ? "check" : r.status === "fail" ? "close" : "alert"} size={12} />
                </span>
                <span>
                  {r.label}
                  {r.detail && <span className="block text-xs text-muted">{r.detail}</span>}
                  {r.status === "skip" && r.required && (
                    <span className="block text-xs text-research">Not verified: this needs attention.</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
