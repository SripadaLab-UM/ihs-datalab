import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { type RunDetail, workflowsApi } from "@/api/workflows";
import { Button, Chip, EmptyNote, Icon, InfoTip } from "@/components/ui";

import { DeliveryStatus } from "./Delivery";
import { runKey, useRun } from "./hooks";
import { ReplayDialog } from "./ReplayDialog";
import { RunSteps } from "./Steps";
import { delivering, isLive, MODE, runPath, runStatus, short, when, workflowPath } from "./words";
import { PageHeader } from "./PageHeader";

/** One run's page: its steps as they go, what it pinned, its delivery, and Run again and Replay. */
export function RunPage({ runId }: { runId: string }) {
  const run = useRun(runId);
  if (run.isError) {
    return (
      <div className="px-6 py-6">
        <p className="font-sans text-[13.5px] text-danger">{run.error.message}</p>
        <Link to="/workflows" className="font-sans text-[13px] text-muted underline decoration-faint underline-offset-4 hover:text-ink hover:decoration-ink">
          Back to the workflows
        </Link>
      </div>
    );
  }
  if (!run.data) return <p className="px-6 py-6 font-sans text-[13px] text-muted">Loading the run…</p>;
  const detail = run.data;
  return (
    <div className="flex flex-col gap-7 px-6 pt-4 pb-10">
      <PageHeader>
        <div className="flex flex-col gap-1">
          <p className="font-sans text-[12.5px] text-muted">
            <Link to="/workflows" className="hover:text-ink">
              Workflows
            </Link>{" "}
            ·{" "}
            <Link to={workflowPath(detail.workflow_path)} className="hover:text-ink">
              {detail.workflow_name}
            </Link>
          </p>
          <h1 className="font-serif text-[23px] leading-tight">
            {MODE[detail.mode]} of {detail.workflow_name}
          </h1>
          <RunLine run={detail} />
        </div>
      </PageHeader>
      <RunProgress run={detail} />
      {detail.mode === "replay" && <ReplayOutcome run={detail} />}
      <section aria-labelledby="delivery-title" className="flex flex-col gap-2">
        <h2 id="delivery-title" className="dl-label">
          Delivery
        </h2>
        <DeliveryStatus run={detail} deliveries={detail.deliveries} />
      </section>
      <Pinned run={detail} />
      <RepeatActions run={detail} />
    </div>
  );
}

/** When it ran, who ran it, how it went. */
export function RunLine({ run }: { run: RunDetail }) {
  const status = runStatus(run.status);
  const live = isLive(run);
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 font-sans text-[13px] text-muted">
      {live ? (
        <span className="flex items-center gap-2 text-ink">
          <span aria-hidden className="dl-breathe size-[7px] rounded-full bg-ink" />
          {delivering(run) ? "Delivering" : run.status === "queued" ? "Waiting to start" : "Running"}
        </span>
      ) : (
        <Chip tone={status.tone}>{status.text}</Chip>
      )}
      <span>
        Started {when(run.started_at)} by {run.started_by}
      </span>
      {run.finished_at && <span>finished {when(run.finished_at)}</span>}
      {run.of_run && (
        <span>
          of{" "}
          <Link to={runPath(run.of_run)} className="font-mono text-[12px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
            {run.of_run}
          </Link>
        </span>
      )}
      <span className="font-mono text-[12px] text-faint">{run.id}</span>
    </p>
  );
}

/** A run's steps, live while it goes, with Stop. */
export function RunProgress({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const [refused, setRefused] = useState<string | null>(null);
  const stop = useMutation({
    mutationFn: () => workflowsApi.stop(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: runKey(run.id) }),
    onError: (error) => setRefused(error.message),
  });
  const live = isLive(run);
  const inDelivery = delivering(run);
  return (
    <section aria-labelledby={`steps-${run.id}`} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id={`steps-${run.id}`} className="dl-label">
          Steps
        </h2>
        {live && !inDelivery && (
          <Button variant="danger" className="px-2.5 py-1 text-[12.5px]" disabled={stop.isPending} onClick={() => stop.mutate()}>
            <Icon name="stop" size={12} /> {stop.isPending ? "Stopping…" : "Stop"}
          </Button>
        )}
      </div>
      {inDelivery && (
        <p role="status" className="font-sans text-[13px] text-muted">
          Every check passed and the files are being delivered. A run can't be stopped once its delivery has started:
          files may already be in the destination, and what was delivered is recorded.
        </p>
      )}
      {refused && live && (
        <p role="alert" className="font-sans text-[13px] text-attn">
          {refused}
        </p>
      )}
      {!live && run.message && (
        <p className={run.status === "failed" ? "font-sans text-[13px] text-danger" : "font-sans text-[13px] text-muted"}>
          {run.message}
        </p>
      )}
      <RunSteps steps={run.steps} />
    </section>
  );
}

/** Whether a Replay reproduced the original: every output byte for byte, and every check the same. */
function ReplayOutcome({ run }: { run: RunDetail }) {
  const live = isLive(run);
  return (
    <section aria-labelledby="replay-title" className="flex flex-col gap-2">
      <h2 id="replay-title" className="dl-label">
        Replay
      </h2>
      {run.replay_exact === false ? (
        <p className="font-sans text-[13px] text-attn">You chose to replay this although it couldn't be exact.</p>
      ) : (
        <p className="font-sans text-[13px] text-muted">
          The original definition, extracts, image and seed, run again.
        </p>
      )}
      {live || run.reproduced === null ? (
        <p className="font-sans text-[13px] text-muted">
          {live ? "Once it finishes, each output is compared with the original's." : "It wasn't compared with the original."}
        </p>
      ) : run.reproduced ? (
        <p role="status" className="flex items-baseline gap-1.5 font-sans text-[13.5px] text-data">
          <Icon name="check" size={13} className="translate-y-[2px]" /> Every output matched the original byte for byte,
          and every check came out the same.
        </p>
      ) : (
        <p role="status" className="flex items-baseline gap-1.5 font-sans text-[13.5px] text-danger">
          <Icon name="alert" size={13} className="translate-y-[2px]" /> Not everything matched the original.
        </p>
      )}
      {run.replay_notes.length > 0 && (
        <ul className="flex list-disc flex-col gap-0.5 pl-5 font-sans text-[13px] text-ink">
          {run.replay_notes.map((note, i) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Everything the run pinned, so it can be told apart from any other and replayed. */
function Pinned({ run }: { run: RunDetail }) {
  const extracts = run.steps.flatMap((step) => {
    const kept: { name: string; file: string; sha256: string; rows?: number }[] = [];
    if (step.kind === "sql" && step.status === "succeeded") {
      const out = (step.outputs as Record<string, { file: string; sha256: string; rows?: number }>).final;
      if (out) kept.push({ name: step.step_id, file: out.file, sha256: out.sha256, rows: out.rows });
    }
    if (step.kind === "pipeline") {
      for (const [name, input] of Object.entries(step.inputs as Record<string, { file: string; sha256: string; folder?: string }>)) {
        if (input.folder === "extracts") kept.push({ name: `${step.step_id} · ${name}`, file: input.file, sha256: input.sha256 });
      }
    }
    return kept;
  });
  const params = Object.entries(run.params);
  return (
    <section aria-labelledby="pinned-title" className="flex flex-col gap-2">
      <h2 id="pinned-title" className="dl-label">
        What this run pinned
      </h2>
      <dl className="grid grid-cols-[minmax(8rem,max-content)_minmax(0,1fr)] gap-x-5 gap-y-1.5 font-sans text-[13px]">
        <dt className="text-muted">Workflow file</dt>
        <dd className="min-w-0 font-mono text-[12px] break-all text-ink">
          {run.workflow_path}
          <br />
          {run.workflow_source === "git" ? (
            <>
              commit {run.repo_commit ?? "not recorded"} · blob {run.workflow_blob}
            </>
          ) : (
            <>not committed · {run.workflow_blob}</>
          )}
        </dd>
        <dt className="text-muted">Agent image</dt>
        <dd className="min-w-0 font-mono text-[12px] break-all text-ink">
          {run.image_ref || "none needed"}
          {run.image_digest && (
            <>
              <br />
              {run.image_digest} · {run.image_platform}
            </>
          )}
        </dd>
        <dt className="text-muted">Seed</dt>
        <dd className="font-mono text-[12px] text-ink">{run.seed}</dd>
        <dt className="text-muted">Parameters</dt>
        <dd className="font-mono text-[12px] text-ink">
          {params.length ? params.map(([k, v]) => `${k} = ${String(v)}`).join(" · ") : "none"}
        </dd>
        <dt className="text-muted">Reads</dt>
        <dd className="font-mono text-[12px] break-all text-ink">{run.reads.join(", ") || "nothing from the database"}</dd>
        <dt className="text-muted">Extracted inputs</dt>
        <dd className="min-w-0 font-sans text-[12.5px]">
          {extracts.length === 0 ? (
            <span className="text-muted">None.</span>
          ) : (
            <ul className="flex flex-col gap-0.5 font-mono text-[12px] text-ink">
              {extracts.map((e) => (
                <li key={e.name} className="flex flex-wrap gap-x-3">
                  <span>{e.file}</span>
                  <span className="text-muted" title={e.sha256}>
                    sha256 {short(e.sha256)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-muted">
            {run.inputs_kept
              ? "Kept in the run folder on this computer, for Replay."
              : "No longer kept, so this run can't be replayed."}
          </p>
        </dd>
        {run.pipelines.length > 0 && (
          <>
            <dt className="text-muted">Pipelines</dt>
            <dd className="font-mono text-[12px] break-all text-ink">
              {run.pipelines.map((p, i) => (
                <span key={i} className="block">
                  {String(p.name)} · source {short(String(p.tree_sha256))} · library {short(String(p.library_sha256))}
                </span>
              ))}
            </dd>
          </>
        )}
        <dt className="text-muted">DataLab</dt>
        <dd className="font-mono text-[12px] break-all text-muted">
          {run.runner_version}
          {run.r_packages_sha256 && <> · R packages {short(run.r_packages_sha256)}</>} · Docker on {run.host_platform}
        </dd>
      </dl>
    </section>
  );
}

/** Run again (today's data) and Replay (the original extracts). */
function RepeatActions({ run }: { run: RunDetail }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [replaying, setReplaying] = useState(false);
  // Run again runs the file as it is now, so it delivers where the file says now.
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: workflowsApi.list });
  const current = workflows.data?.find((w) => w.path === run.workflow_path);
  const again = useMutation({
    mutationFn: () => workflowsApi.again(run.id),
    onSuccess: (started) => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
      navigate(runPath(started.id));
    },
  });
  if (isLive(run)) return null;
  return (
    <section aria-labelledby="repeat-title" className="flex flex-col gap-3">
      <div className="flex items-center gap-1">
        <h2 id="repeat-title" className="dl-label">
          Repeat this run
        </h2>
        <InfoTip term="replay" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col items-start gap-1.5">
          <Button onClick={() => again.mutate()} disabled={again.isPending}>
            <Icon name="restore" size={13} /> {again.isPending ? "Starting…" : "Run again"}
          </Button>
          <p className="font-sans text-[12.5px] text-muted">
            The workflow file as it is now, with the same parameters and seed, on today's data: new results
            {current?.deliver ? (
              <>
                , delivered to <span className="font-mono text-ink">{current.deliver.destination}</span> if every check
                passes.
              </>
            ) : current ? (
              ". It doesn't deliver anything."
            ) : (
              ", delivered wherever the file says now if every check passes."
            )}
          </p>
          {again.error && <p className="font-sans text-[12.5px] text-danger">{again.error.message}</p>}
        </div>
        <div className="flex flex-col items-start gap-1.5">
          <Button onClick={() => setReplaying(true)}>
            <Icon name="history" size={13} /> Replay…
          </Button>
          <p className="font-sans text-[12.5px] text-muted">
            This run's own definition, extracts, image and seed: the same results, checked byte for byte.
          </p>
        </div>
      </div>
      {replaying && <ReplayDialog run={run} onClose={() => setReplaying(false)} />}
    </section>
  );
}

export function NoRunsYet() {
  return (
    <EmptyNote icon="history" title="No runs yet">
      Each run appears here with its steps, what it pinned and where it delivered. Runs stay on this computer.
    </EmptyNote>
  );
}
