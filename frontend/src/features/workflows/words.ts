// How the Workflows tab says things: statuses, step kinds, times. No components.
import type { RunDetail, RunStep, Workflow, WorkflowProblem, WorkflowRun } from "@/api/workflows";

export const runPath = (runId: string) => `/workflows/runs/${encodeURIComponent(runId)}`;
export const workflowPath = (path: string) => `/workflows/file?path=${encodeURIComponent(path)}`;

export type Tone = "good" | "attn" | "bad" | "you" | undefined;

export const STEP_KIND: Record<RunStep["kind"], string> = {
  sql: "SQL extract",
  r: "R",
  pipeline: "Pipeline",
  qc_builtin: "Checks",
  qc_custom: "Custom check",
};

export const MODE: Record<WorkflowRun["mode"], string> = {
  run: "Run",
  run_again: "Run again",
  replay: "Replay",
};

export function runStatus(status: WorkflowRun["status"]): { text: string; tone: Tone } {
  switch (status) {
    case "queued":
      return { text: "waiting to start", tone: undefined };
    case "running":
      return { text: "running", tone: undefined };
    case "succeeded":
      return { text: "succeeded", tone: "good" };
    case "failed":
      return { text: "failed", tone: "bad" };
    case "cancelled":
      return { text: "stopped", tone: undefined };
    case "interrupted":
      return { text: "interrupted", tone: "attn" };
  }
}

export function stepStatus(status: RunStep["status"]): { text: string; tone: Tone } {
  switch (status) {
    case "pending":
      return { text: "waiting", tone: undefined };
    case "running":
      return { text: "running", tone: undefined };
    case "succeeded":
      return { text: "done", tone: "good" };
    case "failed":
      return { text: "failed", tone: "bad" };
    case "skipped":
      return { text: "didn't run", tone: undefined };
    case "cancelled":
      return { text: "stopped", tone: undefined };
  }
}

/** Still going: a run is marked finished only after its delivery (the status is set just before). */
export const isLive = (run: Pick<WorkflowRun, "status" | "finished_at">) =>
  run.status === "queued" || run.status === "running" || !run.finished_at;

/** Every step has passed and the run is delivering: Stop is refused from here. */
export function delivering(run: RunDetail): boolean {
  return (
    isLive(run) &&
    run.delivery_status === "pending" &&
    run.steps.length > 0 &&
    run.steps.every((s) => s.status === "succeeded")
  );
}

/** The problems that hold up delivery: a delivered file with no small-cells check and no reason given. */
export function deliveryProblems(workflow: Pick<Workflow, "problems">): WorkflowProblem[] {
  return workflow.problems.filter((p) => p.path === "deliver" || p.path.startsWith("deliver."));
}

/** Problems in the order they come in the file; those about the whole file first. */
export function byLine<T extends WorkflowProblem>(problems: T[]): T[] {
  return [...problems].sort((a, b) => (a.line ?? 0) - (b.line ?? 0));
}

/** Where a problem is, as a person would look for it: "line 12 · steps[1].inputs.raw". */
export function problemWhere(problem: WorkflowProblem): string {
  const parts = [];
  if (problem.line) parts.push(`line ${problem.line}`);
  if (problem.path && !/^line \d+$/.test(problem.path)) parts.push(problem.path);
  return parts.join(" · ") || "the whole file";
}

const DAY = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DAY.format(date);
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

/** A checksum shortened for reading; the whole one is in the title. */
export const short = (hash: string | null | undefined) => (hash ? hash.replace(/^sha256:/, "").slice(0, 12) : "");

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
