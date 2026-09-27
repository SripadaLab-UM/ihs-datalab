import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { type PipelineFinding, type PipelineProposalDetail, pipelinesApi } from "@/api/pipelines";
import { DiffView } from "@/components/editor/DiffView";
import { Button, Chip, Icon } from "@/components/ui";

import { ACTIONABLE, busy, languageOf, proposalChip, when } from "./pipelines";
import { TestResults } from "./TestResults";

/**
 * One proposed change to the pipelines repo: what the agent changed, file by
 * file; the package's tests on it; the check; and Save & share or Discard.
 * Nothing here edits the change: to change it, ask the agent.
 */
export function ProposalView({ id, onBack }: { id: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["pipeline-proposal", id],
    queryFn: () => pipelinesApi.proposal(id),
    // While its tests or its save run in the background.
    refetchInterval: (query) => (query.state.data && busy(query.state.data.proposal) ? 1500 : false),
  });
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const settle = (data: PipelineProposalDetail) => {
    queryClient.setQueryData(["pipeline-proposal", id], data);
    void queryClient.invalidateQueries({ queryKey: ["pipeline-proposals"] });
  };
  const test = useMutation({ mutationFn: () => pipelinesApi.test(id), onSuccess: settle });
  const accept = useMutation({ mutationFn: () => pipelinesApi.accept(id, [...confirmed]), onSuccess: settle });
  const reject = useMutation({ mutationFn: () => pipelinesApi.reject(id), onSuccess: settle });
  const failed = test.error ?? accept.error ?? reject.error;

  if (!detail.data) {
    return (
      <p className="px-5 py-6 font-sans text-[13px] text-muted">
        {detail.isError ? "This proposal couldn't be loaded." : "Loading the proposal…"}
      </p>
    );
  }
  const { proposal, files, findings } = detail.data;
  const chip = proposalChip(proposal);
  const actionable = ACTIONABLE.has(proposal.status);
  const working = busy(proposal) || test.isPending || accept.isPending || reject.isPending;
  const data = findings.filter((f) => f.severity === "data");
  const errors = findings.filter((f) => f.severity === "error");
  const unconfirmed = data.filter((f) => !confirmed.has(f.id));
  const canSave = actionable && !working && files.length > 0 && errors.length === 0 && unconfirmed.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 pt-4 pb-8">
      <button
        type="button"
        onClick={onBack}
        className="mb-3 self-start font-sans text-[12.5px] text-muted underline decoration-faint underline-offset-2 hover:text-ink"
      >
        Back to the files
      </button>
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-serif text-[21px] leading-tight">
          {files.length === 1 ? `A change to ${files[0].path.split("/").pop()}` : `A change to ${files.length} files`}
        </h2>
        <Chip tone={chip.tone}>{chip.text}</Chip>
      </header>
      <p className="mt-1 font-sans text-[13px] text-muted">
        Proposed by the agent in “{proposal.conversation_title ?? proposal.conversation_id}”, after turn {proposal.turn},{" "}
        {when(proposal.created_at)}. On <span className="font-mono text-[12px]">{proposal.base.slice(0, 7)}</span> of main.
      </p>

      <Outcome detail={detail.data} />

      <section aria-labelledby={`${id}-tests`} className="mt-5 border-t border-line pt-4">
        <h3 id={`${id}-tests`} className="dl-label mb-2">
          Tests
        </h3>
        <TestResults test={proposal.test} />
        {actionable && files.length > 0 && proposal.test?.status !== "running" && (
          <Button className="mt-3" onClick={() => test.mutate()} disabled={working}>
            <Icon name="check" size={13} /> {proposal.test ? "Run the tests again" : "Run the tests"}
          </Button>
        )}
      </section>

      {findings.length > 0 && (
        <section aria-labelledby={`${id}-check`} className="mt-5 border-t border-line pt-4">
          <h3 id={`${id}-check`} className="dl-label mb-2">
            Check
          </h3>
          <Findings errors={errors} data={data} confirmed={confirmed} onConfirm={setConfirmed} disabled={!actionable || working} />
        </section>
      )}

      {actionable && (
        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button variant="primary" onClick={() => accept.mutate()} disabled={!canSave}>
            <Icon name="send" size={13} /> Save &amp; share
          </Button>
          <Button variant="danger" onClick={() => reject.mutate()} disabled={working}>
            Discard
          </Button>
          <p className="max-w-[34rem] font-sans text-[12.5px] text-muted">
            {proposal.test?.status === "passed"
              ? "Saves it to GitHub's main as you, once it's checked again with anything others saved meanwhile."
              : "Runs the tests first: it's saved to GitHub's main as you only if they pass."}
          </p>
        </div>
      )}
      {failed && <p className="mt-2 font-sans text-[13px] text-danger">{failed.message}</p>}

      {proposal.refused.length > 0 && (
        <section aria-labelledby={`${id}-refused`} className="mt-5 border-t border-line pt-4">
          <h3 id={`${id}-refused`} className="dl-label mb-2">
            Not proposed
          </h3>
          <ul className="flex flex-col gap-1 font-sans text-[13px] text-muted">
            {proposal.refused.map((r) => (
              <li key={r.path || "all"}>
                {r.path && <span className="font-mono text-[12.5px] text-ink">{r.path}</span>}
                {r.path && ": "}
                {r.reason}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby={`${id}-files`} className="mt-5 border-t border-line pt-4">
        <h3 id={`${id}-files`} className="dl-label mb-2">
          What changed
        </h3>
        {files.length === 0 && <p className="font-sans text-[13px] text-muted">Nothing that can be proposed.</p>}
        <div className="flex flex-col gap-5">
          {files.map((file) => (
            <div key={file.path}>
              <p className="mb-1.5 flex items-center gap-2 font-mono text-[12.5px]">
                <span className="text-ink">{file.path}</span>
                <span className="text-muted">{file.change}</span>
              </p>
              {file.binary ? (
                <p className="font-sans text-[13px] text-muted">Not a text file, so it isn't shown.</p>
              ) : (
                <DiffView
                  label={file.path}
                  original={file.before ?? ""}
                  modified={file.after ?? ""}
                  language={languageOf(file.path)}
                  layout="unified"
                  className="max-h-[70vh] overflow-auto"
                />
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

/** How the last Save & share, or anything else that ended the proposal, went. */
function Outcome({ detail }: { detail: PipelineProposalDetail }) {
  const { proposal } = detail;
  const result = proposal.result;
  if (proposal.status === "saving") {
    return (
      <p className="mt-4 font-sans text-[13.5px] text-muted" aria-live="polite">
        <span className="dl-breathe mr-2 inline-block size-[6px] rounded-full bg-ink" />
        Saving: {proposal.test?.status === "running" ? "the tests are running first." : "checking and sharing it."}
      </p>
    );
  }
  if (!result) return null;
  const tone =
    proposal.status === "saved"
      ? "good"
      : ["conflict", "tests_failed", "failed"].includes(proposal.status)
        ? "bad"
        : proposal.status === "check_failed"
          ? "attn"
          : "muted";
  return (
    <div
      role="status"
      className={clsx(
        "mt-4 rounded-[3px] border px-3 py-2 font-sans text-[13.5px]",
        tone === "good" && "border-data/35 text-data",
        tone === "bad" && "border-danger/40 text-danger",
        tone === "attn" && "border-attn/40 text-attn",
        tone === "muted" && "border-line text-muted",
      )}
    >
      {result.message}
      {proposal.status === "saved" && proposal.commit && (
        <>
          {" "}
          It's commit <span className="font-mono text-[12.5px]">{proposal.commit.slice(0, 7)}</span> on main
          {proposal.decided_by ? `, saved by @${proposal.decided_by}` : ""}.
        </>
      )}
      {result.conflicts.length > 0 && (
        <span className="mt-1 block font-mono text-[12.5px]">{result.conflicts.join(", ")}</span>
      )}
    </div>
  );
}

function Findings({
  errors,
  data,
  confirmed,
  onConfirm,
  disabled,
}: {
  errors: PipelineFinding[];
  data: PipelineFinding[];
  confirmed: Set<string>;
  onConfirm: (next: Set<string>) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 font-sans text-[13px]">
      {errors.length > 0 && (
        <ul className="flex flex-col gap-1 text-danger">
          {errors.map((f) => (
            <li key={f.id}>
              <span className="font-mono text-[12.5px]">{f.path}</span>: {f.message}
            </li>
          ))}
        </ul>
      )}
      {data.length > 0 && (
        <>
          <p className="text-attn">
            This may be participant data. Look at each line, and confirm it isn't before saving (fixtures made up
            for the tests are fine); otherwise ask the agent to take it out.
          </p>
          <ul className="flex flex-col gap-1.5">
            {data.map((f) => (
              <li key={f.id}>
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={confirmed.has(f.id)}
                    disabled={disabled}
                    onChange={(e) => {
                      const next = new Set(confirmed);
                      if (e.target.checked) next.add(f.id);
                      else next.delete(f.id);
                      onConfirm(next);
                    }}
                  />
                  <span>
                    <span className="font-mono text-[12.5px] text-ink">
                      {f.path}
                      {f.line ? `:${f.line}` : ""}
                    </span>{" "}
                    {f.message} <span className="text-muted">I've checked: it isn't participant data.</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
