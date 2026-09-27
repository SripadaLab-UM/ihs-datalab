import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { ApiError } from "@/api/http";
import { type RunDetail, workflowsApi } from "@/api/workflows";
import { Button, Icon, Modal } from "@/components/ui";

import { runPath } from "./words";

/**
 * Replay a run: first the replay check's honest list of what can't be made
 * exact, then an explicit choice to go ahead anyway, and a second, separate
 * one before a replay delivers anything.
 */
export function ReplayDialog({ run, onClose }: { run: RunDetail; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const check = useQuery({
    queryKey: ["workflow-replay-check", run.id],
    queryFn: () => workflowsApi.replayCheck(run.id),
    staleTime: 0,
    gcTime: 0,
  });
  const [inexactOk, setInexactOk] = useState(false);
  const [deliver, setDeliver] = useState(false);
  const [confirmDelivery, setConfirmDelivery] = useState(false);
  // What the replay itself found that the check didn't (something changed in between).
  const [late, setLate] = useState<string[]>([]);
  const replay = useMutation({
    mutationFn: () => workflowsApi.replay(run.id, { allow_inexact: inexactOk, deliver }),
    onSuccess: (started) => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
      onClose();
      navigate(runPath(started.id));
    },
    onError: (error) => {
      const reasons = error instanceof ApiError ? (error.detail as { reasons?: unknown })?.reasons : undefined;
      if (Array.isArray(reasons)) {
        setLate(reasons.map(String));
        setInexactOk(false);
        setConfirmDelivery(false);
      }
    },
  });

  const reasons = [...new Set([...(check.data?.reasons ?? []), ...late])];
  const blocking = check.data?.blocking ?? [];
  const exact = check.data !== undefined && reasons.length === 0 && blocking.length === 0;
  const canDeliver = run.delivery_status !== "none";
  const destination = run.deliveries[0]?.destination_key;
  const ready = check.isSuccess && blocking.length === 0 && (reasons.length === 0 || inexactOk);

  const start = () => {
    if (deliver && !confirmDelivery) return setConfirmDelivery(true);
    replay.mutate();
  };

  return (
    <Modal title="Replay this run" onClose={onClose}>
      <div className="flex flex-col gap-4 font-sans text-[13.5px]">
        <p className="text-muted">
          A Replay reruns this run's own definition on the inputs it extracted, with its image and seed. Nothing is read
          from the database again.
        </p>

        {check.isPending && <p className="text-muted">Checking whether it can be exact…</p>}
        {check.isError && <p className="text-danger">{check.error.message}</p>}

        {blocking.length > 0 && (
          <div role="alert">
            <p className="font-medium text-danger">This run can't be replayed:</p>
            <ul className="mt-1 list-disc pl-5 text-ink">
              {blocking.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
        )}

        {exact && (
          <p role="status" className="flex items-baseline gap-1.5 text-data">
            <Icon name="check" size={13} className="translate-y-[2px]" /> This Replay can be exact: everything the run
            pinned is still here.
          </p>
        )}

        {blocking.length === 0 && reasons.length > 0 && (
          <div role="alert" className="flex flex-col gap-2">
            <p className="font-medium text-attn">This Replay can't be exact:</p>
            <ul className="list-disc pl-5 text-ink">
              {reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            <label className="flex items-baseline gap-2">
              <input
                type="checkbox"
                checked={inexactOk}
                onChange={(e) => setInexactOk(e.target.checked)}
                className="translate-y-[2px]"
              />
              <span>Replay anyway. I understand its results may not match the original, and why.</span>
            </label>
          </div>
        )}

        {check.isSuccess && blocking.length === 0 && canDeliver && (
          <div className="flex flex-col gap-1.5 border-t border-line pt-3">
            <label className="flex items-baseline gap-2">
              <input
                type="checkbox"
                checked={deliver}
                onChange={(e) => {
                  setDeliver(e.target.checked);
                  setConfirmDelivery(false);
                }}
                className="translate-y-[2px]"
              />
              <span>Deliver the replay's outputs too</span>
            </label>
            <p className="pl-5 text-[12.5px] text-muted">
              Off unless you choose it. A replay that delivers copies its files to{" "}
              {destination ? <span className="font-mono text-ink">{destination}</span> : "the workflow's destination"}{" "}
              in a new dated folder, once every check has passed.
            </p>
          </div>
        )}

        {confirmDelivery && (
          <div role="alert" className="border-l-2 border-you pl-3">
            <p className="font-medium text-you">Deliver this replay?</p>
            <p className="text-ink">
              Its files will leave DataLab for{" "}
              {destination ? <span className="font-mono">{destination}</span> : "the workflow's destination"}, as if it
              were a new run. Choose Replay and deliver again to go ahead.
            </p>
          </div>
        )}

        {replay.error && !late.length && <p className="text-danger">{replay.error.message}</p>}
        {late.length > 0 && !inexactOk && (
          <p className="text-attn">Something changed since the check: read the list above again before you go on.</p>
        )}

        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!ready || replay.isPending} onClick={start}>
            {replay.isPending
              ? "Starting…"
              : deliver
                ? confirmDelivery
                  ? "Yes, replay and deliver"
                  : "Replay and deliver…"
                : reasons.length
                  ? "Replay, not exact"
                  : "Replay"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
