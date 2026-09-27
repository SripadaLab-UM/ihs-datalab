import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { type Delivery, type Workflow, type WorkflowRun, workflowsApi } from "@/api/workflows";
import { Chip, Icon } from "@/components/ui";

import { deliveryProblems, isLive, plural, problemWhere, short, when } from "./words";

/** What a run's delivery did, or is waiting for, in plain words. */
export function DeliveryStatus({
  run,
  deliveries = [],
}: {
  run: Pick<WorkflowRun, "delivery_status" | "delivery_message" | "status" | "finished_at" | "mode">;
  deliveries?: Delivery[];
}) {
  const live = isLive(run);
  switch (run.delivery_status) {
    case "none":
      return <p className="font-sans text-[13px] text-muted">This workflow doesn't deliver anything.</p>;
    case "pending":
      return (
        <p className="font-sans text-[13px] text-muted">
          {live
            ? "Waiting: files are delivered only once every step and check has passed."
            : "Not delivered."}
        </p>
      );
    case "skipped":
      return (
        <p className="font-sans text-[13px] text-ink">
          Nothing was delivered. <span className="text-muted">{run.delivery_message}</span>
        </p>
      );
    case "failed":
      return (
        <p role="alert" className="flex items-baseline gap-1.5 font-sans text-[13px] text-danger">
          <Icon name="alert" size={13} className="shrink-0 translate-y-[2px]" />
          <span>The delivery didn't complete. {run.delivery_message}</span>
        </p>
      );
    case "delivered":
      return (
        <div className="flex flex-col gap-2">
          <p className="flex items-baseline gap-1.5 font-sans text-[13px] text-data">
            <Icon name="check" size={13} className="translate-y-[2px]" /> {run.delivery_message ?? "Delivered."}
          </p>
          {deliveries.map((d) => (
            <DeliveryFiles key={d.id} delivery={d} />
          ))}
        </div>
      );
  }
}

function DeliveryFiles({ delivery }: { delivery: Delivery }) {
  return (
    <div className="border-l border-line pl-3 font-sans text-[12.5px] text-muted">
      <p>
        To <span className="font-mono text-ink">{delivery.destination_key}</span>, in{" "}
        <span className="font-mono text-ink break-all">{delivery.folder}</span>, {when(delivery.delivered_at)}
      </p>
      <ul className="mt-1 flex flex-col gap-0.5 font-mono text-[12px]">
        {delivery.files.map((file) => (
          <li key={String(file.path)} className="flex flex-wrap gap-x-3">
            <span className="text-ink">{String(file.path)}</span>
            {typeof file.bytes === "number" && <span>{plural(file.bytes, "byte")}</span>}
            <span title={String(file.sha256)}>sha256 {short(String(file.sha256))}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 font-mono text-[12px]" title={delivery.manifest_sha256}>
        manifest sha256 {short(delivery.manifest_sha256)}
      </p>
    </div>
  );
}

/** Why a workflow can't deliver yet: a delivered file needs a small-cells check, or a reason it doesn't. */
export function DeliveryBlocked({ workflow }: { workflow: Pick<Workflow, "problems"> }) {
  const problems = deliveryProblems(workflow);
  if (problems.length === 0) return null;
  return (
    <div role="alert" className="border-l-2 border-attn pl-3 font-sans text-[13px]">
      <p className="font-medium text-attn">Delivery is blocked, so this workflow can't run yet.</p>
      <ul className="mt-1 flex flex-col gap-1 text-ink">
        {problems.map((p, i) => (
          <li key={i}>
            <span className="font-mono text-[12px] text-muted">{problemWhere(p)}</span> {p.message}
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-muted">
        Add a <code className="font-mono text-[12px]">small_cells</code> check over each delivered CSV, or give the
        reason it isn't needed under <code className="font-mono text-[12px]">deliver.without_small_cells</code>.
      </p>
    </div>
  );
}

/** The destination keys workflow files name, and where each goes on this computer. Read-only: folders are chosen in Settings. */
export function Destinations({ practice }: { practice: boolean }) {
  const keys = useQuery({ queryKey: ["workflow-destinations"], queryFn: workflowsApi.destinations });
  return (
    <section aria-labelledby="destinations-title" className="flex flex-col gap-2">
      <h2 id="destinations-title" className="dl-label">
        Destinations
      </h2>
      <p className="max-w-[46rem] font-sans text-[13px] text-muted">
        {practice ? (
          "Practice DataLab delivers only to its own practice folder, so nothing from it ends up somewhere real."
        ) : (
          <>
            A workflow names a destination by a key. Each computer chooses which of its export folders a key means, in{" "}
            <Link to="/settings" className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
              Settings &amp; Safety
            </Link>
            .
          </>
        )}
      </p>
      {keys.isError && <p className="font-sans text-[13px] text-danger">{keys.error.message}</p>}
      {keys.data?.length === 0 && (
        <p className="font-sans text-[13px] text-muted">No workflow here delivers anywhere yet.</p>
      )}
      {keys.data && keys.data.length > 0 && (
        <ul className="flex flex-col border-t border-line">
          {keys.data.map((key) => (
            <li key={key.key} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line py-2">
              <span className="font-mono text-[12.5px] text-ink">{key.key}</span>
              <span className="min-w-0 flex-1 font-sans text-[13px] text-muted">
                {key.path ? (
                  <>
                    {key.name} · <span className="font-mono text-[12px] break-all">{key.path}</span>
                  </>
                ) : (
                  "No folder chosen on this computer yet."
                )}
              </span>
              {key.available ? (
                <Chip tone="good">ready</Chip>
              ) : (
                <Chip tone="attn" title="Runs that deliver here fail at delivery until a folder is chosen.">
                  {key.path ? "folder not found" : "not set"}
                </Chip>
              )}
              {key.used_by.length > 0 && (
                <span className="w-full font-sans text-[12px] text-faint">
                  Used by {key.used_by.join(", ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
