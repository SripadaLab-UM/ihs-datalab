import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/http";
import {
  type ParamValue,
  type Workflow,
  type WorkflowParameter,
  type WorkflowProblem,
  type WorkflowRun,
  workflowsApi,
} from "@/api/workflows";
import { CodeEditor, type EditorDiagnostic } from "@/components/editor/CodeEditor";
import { Button, Chip, Icon } from "@/components/ui";

import { DeliveryBlocked } from "./Delivery";
import { useRun } from "./hooks";
import { NoRunsYet, RunLine, RunProgress } from "./RunView";
import { byLine, isLive, MODE, problemWhere, runPath, runStatus, STEP_KIND, when } from "./words";
import { PageHeader } from "./PageHeader";

/** One workflow: its file (read-only), its checks, a form to run it, the run as it goes, and its history. */
export function WorkflowView({ workflow }: { workflow: Workflow }) {
  const text = useQuery({ queryKey: ["workflow-text", workflow.path], queryFn: () => workflowsApi.text(workflow.path) });
  const [current, setCurrent] = useState<string | null>(null);
  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      workflow.problems.map((p) => ({
        line: p.line ?? 1,
        column: p.column ?? undefined,
        message: p.path && !p.line ? `${p.path}: ${p.message}` : p.message,
        severity: "error",
      })),
    [workflow.problems],
  );

  return (
    <div className="flex flex-col gap-7 px-6 pt-4 pb-10">
      <PageHeader>
        <div className="flex flex-col gap-1">
          <p className="font-sans text-[12.5px] text-muted">
            <Link to="/workflows" className="hover:text-ink">
              Workflows
            </Link>
          </p>
          <h1 className="font-serif text-[23px] leading-tight">{workflow.name ?? workflow.path}</h1>
          {workflow.description && <p className="max-w-[46rem] font-serif text-[16px] text-ink">{workflow.description}</p>}
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 font-sans text-[12.5px] text-muted">
            <span className="font-mono text-[12px]">{workflow.path}</span>
            {workflow.valid ? (
              <Chip tone="good">passes its checks</Chip>
            ) : (
              <Chip tone="bad">{workflow.problems.length === 1 ? "1 problem" : `${workflow.problems.length} problems`}</Chip>
            )}
            {workflow.source === "git" ? (
              <span className="font-mono text-[12px]" title={workflow.blob ?? ""}>
                commit {workflow.commit?.slice(0, 10)}
              </span>
            ) : (
              workflow.source === "file" && <span>not committed to git</span>
            )}
          </p>
        </div>
      </PageHeader>

      {!workflow.valid && <Problems problems={workflow.problems} />}
      <DeliveryBlocked workflow={workflow} />

      {workflow.valid && (
        <section aria-labelledby="run-title" className="flex flex-col gap-3">
          <h2 id="run-title" className="dl-label">
            Run
          </h2>
          <RunForm workflow={workflow} onStarted={(run) => setCurrent(run.id)} />
          {current && <CurrentRun runId={current} />}
        </section>
      )}

      {workflow.valid && workflow.steps.length > 0 && (
        <section aria-labelledby="plan-title" className="flex flex-col gap-2">
          <h2 id="plan-title" className="dl-label">
            What it does
          </h2>
          <ol className="flex flex-col border-t border-line font-sans text-[13.5px]">
            {workflow.steps.map((step) => (
              <li key={step.id} className="flex flex-wrap items-baseline gap-x-3 border-b border-line py-2">
                <span className="font-mono text-[12.5px] text-ink">{step.id}</span>
                <span className="text-muted">{STEP_KIND[step.kind]}</span>
                {Object.keys(step.inputs).length > 0 && (
                  <span className="font-sans text-[12.5px] text-faint">reads {Object.values(step.inputs).join(", ")}</span>
                )}
                {Object.keys(step.outputs).length > 0 && (
                  <span className="font-mono text-[12px] text-muted">→ {Object.values(step.outputs).join(", ")}</span>
                )}
                {step.description && <span className="w-full text-muted">{step.description}</span>}
              </li>
            ))}
            {workflow.deliver && (
              <li className="flex flex-wrap items-baseline gap-x-3 py-2">
                <span className="font-mono text-[12.5px] text-ink">deliver</span>
                <span className="text-muted">
                  {workflow.deliver.files.join(", ")} to <span className="font-mono text-[12.5px] text-ink">{workflow.deliver.destination}</span>
                  , in <span className="font-mono text-[12.5px]">{workflow.deliver.folder}</span>, once every check has passed
                </span>
              </li>
            )}
          </ol>
          {workflow.reads.length > 0 && (
            <p className="font-sans text-[12.5px] text-muted">
              Reads only <span className="font-mono text-[12px] text-ink">{workflow.reads.join(", ")}</span> from the
              database.
            </p>
          )}
        </section>
      )}

      <section aria-labelledby="file-title" className="flex flex-col gap-2">
        <h2 id="file-title" className="dl-label">
          The file
        </h2>
        <p className="font-sans text-[12.5px] text-muted">
          {workflow.builtin
            ? "Read-only: it comes with Practice DataLab. To make your own version, start a New workflow on the Workflows page."
            : "Read-only here. New workflows come from New workflow on the Workflows page, Save as workflow in the SQL Playground, or Turn this into a workflow in a conversation; to change this one, edit the file in its folder (or, in the pipelines repo, ask the Workflow authoring agent) and it's checked again when you come back."}
        </p>
        {text.data && text.data.blob !== workflow.blob && workflow.problems.length > 0 && (
          <p className="font-sans text-[12.5px] text-attn">
            The file has changed since it was checked, so its problems aren't marked. Come back to this page to check it
            again.
          </p>
        )}
        {text.isError ? (
          <p className="font-sans text-[13px] text-danger">{text.error.message}</p>
        ) : (
          <CodeEditor
            label={`Workflow file ${workflow.path}`}
            language="yaml"
            value={text.data?.text ?? ""}
            readOnly
            // Marks only on the text they were found in: the file may have changed since it was checked.
            diagnostics={text.data && text.data.blob === workflow.blob ? diagnostics : undefined}
            className="h-[clamp(14rem,48vh,36rem)]"
          />
        )}
      </section>

      <RunHistory path={workflow.path} />
    </div>
  );
}

/** The file's problems, each with where it is. */
function Problems({ problems }: { problems: WorkflowProblem[] }) {
  return (
    <section aria-labelledby="problems-title" className="flex flex-col gap-2">
      <h2 id="problems-title" className="dl-label text-danger">
        Why it can't run
      </h2>
      <ul className="flex flex-col border-t border-line">
        {byLine(problems).map((p, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-3 border-b border-line py-2 font-sans text-[13.5px]">
            <span className="font-mono text-[12px] text-danger">{problemWhere(p)}</span>
            <span className="text-ink">{p.message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** A form made from the workflow's parameters, and Run. */
function RunForm({ workflow, onStarted }: { workflow: Workflow; onStarted: (run: WorkflowRun) => void }) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, ParamValue>>(() =>
    Object.fromEntries(workflow.parameters.map((p) => [p.name, initial(p)])),
  );
  // Fields the browser couldn't read as a number ("1e"): it reports them as empty,
  // which would quietly run with the default instead.
  const [unreadable, setUnreadable] = useState<Record<string, string>>({});
  const [tried, setTried] = useState(false);
  const start = useMutation({
    mutationFn: () => workflowsApi.start(workflow.path, typed(workflow.parameters, values)),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      onStarted(run);
    },
  });
  const problems = start.error instanceof ApiError ? problemsOf(start.error) : [];
  const byParam = new Map(problems.filter((p) => p.path.startsWith("params.")).map((p) => [p.path.slice(7), p.message]));
  for (const [name, message] of Object.entries(unreadable)) byParam.set(name, message);
  const blocked = Object.keys(unreadable).length > 0;
  const other = problems.filter((p) => !p.path.startsWith("params."));

  return (
    <form
      noValidate
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (!blocked) start.mutate();
      }}
    >
      {workflow.parameters.length > 0 ? (
        <fieldset className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-x-5 gap-y-3">
          <legend className="sr-only">Parameters</legend>
          {workflow.parameters.map((p) => (
            <ParamField
              key={p.name}
              parameter={p}
              value={values[p.name]}
              problem={byParam.get(p.name)}
              onChange={(value, problem) => {
                setValues({ ...values, [p.name]: value });
                const { [p.name]: _, ...rest } = unreadable;
                setUnreadable(problem ? { ...rest, [p.name]: problem } : rest);
              }}
            />
          ))}
        </fieldset>
      ) : (
        <p className="font-sans text-[13px] text-muted">This workflow takes no parameters.</p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={start.isPending}>
          <Icon name="chevron" size={13} /> {start.isPending ? "Starting…" : "Run"}
        </Button>
        <span className="font-sans text-[12.5px] text-muted">
          No AI is involved: DataLab runs each step as written.
          {workflow.deliver && " Files are delivered only if every check passes."}
        </span>
      </div>
      {tried && blocked && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          Nothing was run: correct the values marked above first.
        </p>
      )}
      {start.error && !blocked && (other.length > 0 || byParam.size === 0) && (
        <div role="alert" className="font-sans text-[13px] text-danger">
          <p>{start.error.message}</p>
          {other.length > 0 && (
            <ul className="mt-1 flex flex-col gap-0.5">
              {other.map((p, i) => (
                <li key={i}>
                  <span className="font-mono text-[12px]">{p.path}</span> {p.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}

function problemsOf(error: ApiError): { path: string; message: string }[] {
  const problems = (error.detail as { problems?: unknown } | undefined)?.problems;
  return Array.isArray(problems) ? (problems as { path: string; message: string }[]) : [];
}

function initial(p: WorkflowParameter): ParamValue {
  if (p.type === "boolean") return p.default === true;
  return p.default === null || p.default === undefined ? "" : String(p.default);
}

/** The form's text as the parameter types; an empty field is left out, so the file's default applies. */
function typed(parameters: WorkflowParameter[], values: Record<string, ParamValue>): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const p of parameters) {
    const value = values[p.name];
    if (p.type === "boolean") out[p.name] = Boolean(value);
    else if (value === "" || value === undefined) continue;
    else if (p.type === "integer" || p.type === "number") {
      const n = Number(value);
      out[p.name] = Number.isFinite(n) && String(value).trim() !== "" ? n : String(value);
    } else out[p.name] = String(value);
  }
  return out;
}

function unreadableAs(p: WorkflowParameter, validity: ValidityState): string | undefined {
  if (validity.badInput) return p.type === "date" ? "This isn't a whole date." : "This isn't a number.";
  if (p.type === "integer" && validity.stepMismatch) return "Give a whole number.";
  return undefined;
}

function ParamField({
  parameter: p,
  value,
  problem,
  onChange,
}: {
  parameter: WorkflowParameter;
  value: ParamValue | undefined;
  problem?: string;
  /** With what's wrong with the value as typed, if the browser couldn't read it. */
  onChange: (value: ParamValue, problem?: string) => void;
}) {
  const field = clsx(
    "w-full rounded-[3px] border bg-field px-2 py-1 font-mono text-[12.5px] outline-none focus:border-ink",
    problem ? "border-danger" : "border-line",
  );
  const hint = (
    <>
      {p.description && <span className="block font-sans text-[12px] text-muted">{p.description}</span>}
      {problem && <span className="block font-sans text-[12px] text-danger">{problem}</span>}
    </>
  );
  if (p.type === "boolean") {
    return (
      <label className="flex flex-col gap-1">
        <span className="flex items-baseline gap-2">
          <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} className="translate-y-[2px]" />
          <span className="font-mono text-[12.5px] text-ink">{p.name}</span>
        </span>
        {hint}
      </label>
    );
  }
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-baseline gap-2">
        <span className="font-mono text-[12.5px] text-ink">{p.name}</span>
        <span className="font-sans text-[11.5px] text-faint">{p.type}</span>
      </span>
      <input
        type={p.type === "date" ? "date" : p.type === "integer" || p.type === "number" ? "number" : "text"}
        step={p.type === "integer" ? 1 : p.type === "number" ? "any" : undefined}
        value={String(value ?? "")}
        placeholder={p.default === null ? "required" : undefined}
        aria-invalid={problem ? true : undefined}
        onChange={(e) => onChange(e.target.value, unreadableAs(p, e.target.validity))}
        className={field}
      />
      {hint}
    </label>
  );
}

/** The run just started here, followed live. */
function CurrentRun({ runId }: { runId: string }) {
  const run = useRun(runId);
  if (!run.data) return <p className="font-sans text-[13px] text-muted">Starting…</p>;
  return (
    <div className="flex flex-col gap-3 border-l-2 border-line pl-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <RunLine run={run.data} />
        <Link to={runPath(runId)} className="font-sans text-[12.5px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
          Open this run's page
        </Link>
      </div>
      <RunProgress run={run.data} />
    </div>
  );
}

/** This workflow's runs, newest first. */
function RunHistory({ path }: { path: string }) {
  const runs = useQuery({ queryKey: ["workflow-runs", path], queryFn: () => workflowsApi.runs(path) });
  return (
    <section aria-labelledby="history-title" className="flex flex-col gap-2">
      <h2 id="history-title" className="dl-label">
        Runs
      </h2>
      {runs.isError && <p className="font-sans text-[13px] text-danger">{runs.error.message}</p>}
      {runs.data?.length === 0 && <NoRunsYet />}
      {runs.data && runs.data.length > 0 && <RunList runs={runs.data} />}
    </section>
  );
}

export function RunList({ runs }: { runs: WorkflowRun[] }) {
  return (
    <ul className="flex flex-col border-t border-line">
      {runs.map((run) => {
        const status = runStatus(run.status);
        return (
          <li key={run.id} className="border-b border-line">
            <Link
              to={runPath(run.id)}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 font-sans text-[13px] hover:bg-accent-soft"
            >
              <span className="w-32 shrink-0 text-ink tabular">{when(run.started_at)}</span>
              <span className="w-20 shrink-0 text-muted">{MODE[run.mode]}</span>
              {isLive(run) ? (
                <span className="flex items-center gap-1.5 text-ink">
                  <span aria-hidden className="dl-breathe size-[6px] rounded-full bg-ink" /> running
                </span>
              ) : (
                <Chip tone={status.tone}>{status.text}</Chip>
              )}
              <DeliveryChip run={run} />
              {run.mode === "replay" && run.reproduced !== null && (
                <Chip tone={run.reproduced ? "good" : "bad"}>{run.reproduced ? "matched" : "didn't match"}</Chip>
              )}
              <span className="ml-auto font-mono text-[11.5px] text-faint">{run.id}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function DeliveryChip({ run }: { run: WorkflowRun }) {
  switch (run.delivery_status) {
    case "delivered":
      return <Chip tone="good">delivered</Chip>;
    case "failed":
      return <Chip tone="bad" title={run.delivery_message ?? ""}>delivery failed</Chip>;
    case "skipped":
      return <Chip title={run.delivery_message ?? ""}>not delivered</Chip>;
    case "pending":
      return isLive(run) ? <Chip>delivery waits for checks</Chip> : null;
    default:
      return null;
  }
}
