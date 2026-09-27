import { Link } from "react-router";

import type { Workflow } from "@/api/workflows";
import { Chip, EmptyNote } from "@/components/ui";

import { DeliveryChip } from "./WorkflowView";
import { byLine, deliveryProblems, isLive, MODE, problemWhere, runStatus, when, workflowPath } from "./words";

/** Every workflow in the folder: whether it passes its checks (and if not, where), and how it last ran. */
export function WorkflowList({ workflows, folder }: { workflows: Workflow[]; folder?: string }) {
  if (workflows.length === 0) {
    return (
      <EmptyNote icon="file" title="No workflows yet">
        Workflow files (.yaml) in {folder ? <span className="font-mono text-[12px]">{folder}</span> : "the workflows folder"}{" "}
        appear here, checked and ready to run.
      </EmptyNote>
    );
  }
  return (
    <ul aria-label="Workflows" className="flex flex-col border-t border-line">
      {workflows.map((w) => (
        <li key={w.path} className="border-b border-line">
          <WorkflowRow workflow={w} />
        </li>
      ))}
    </ul>
  );
}

function WorkflowRow({ workflow: w }: { workflow: Workflow }) {
  const blocked = deliveryProblems(w);
  const others = w.problems.filter((p) => !blocked.includes(p));
  const last = w.last_run;
  return (
    <div className="flex flex-col gap-1.5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link
          to={workflowPath(w.path)}
          className="font-serif text-[17px] text-ink underline decoration-transparent underline-offset-4 hover:decoration-ink"
        >
          {w.name ?? w.path}
        </Link>
        <span className="font-mono text-[11.5px] text-faint">{w.path}</span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          {w.valid ? (
            <Chip tone="good">ready to run</Chip>
          ) : blocked.length > 0 && others.length === 0 ? (
            <Chip tone="attn">delivery blocked</Chip>
          ) : (
            <Chip tone="bad">{w.problems.length === 1 ? "1 problem" : `${w.problems.length} problems`}</Chip>
          )}
        </span>
      </div>
      {w.description && <p className="font-sans text-[13px] text-muted">{w.description}</p>}
      {w.problems.length > 0 && (
        <ul className="flex flex-col gap-0.5 font-sans text-[12.5px]">
          {byLine(w.problems).map((p, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-2">
              <span className={`font-mono text-[11.5px] ${blocked.includes(p) ? "text-attn" : "text-danger"}`}>
                {problemWhere(p)}
              </span>
              <span className="text-ink">{p.message}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 font-sans text-[12.5px] text-muted">
        {last ? (
          <>
            <span>
              Last {MODE[last.mode].toLowerCase()} {when(last.started_at)}
            </span>
            {isLive(last) ? (
              <span className="flex items-center gap-1.5 text-ink">
                <span aria-hidden className="dl-breathe size-[6px] rounded-full bg-ink" /> running
              </span>
            ) : (
              <Chip tone={runStatus(last.status).tone}>{runStatus(last.status).text}</Chip>
            )}
            <DeliveryChip run={last} />
          </>
        ) : (
          <span>Not run yet.</span>
        )}
      </p>
    </div>
  );
}
