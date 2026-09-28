import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { type PracticeDatabase as Shown, settingsApi } from "@/api/settings";
import { Button, Chip, Icon, Modal } from "@/components/ui";

export const PRACTICE_DATABASE = ["practice-database"];

/** Words for each phase, as the chip beside the database says them. */
export const PHASE: Record<Shown["phase"], { text: string; tone?: "good" | "attn" | "bad" }> = {
  checking: { text: "Checking…" },
  downloading: { text: "Downloading…" },
  creating: { text: "Setting up…" },
  starting: { text: "Starting…" },
  loading: { text: "Loading the made-up data…" },
  resetting: { text: "Resetting…" },
  ready: { text: "Running", tone: "good" },
  problem: { text: "Not running", tone: "attn" },
};

const SETTLED: Shown["phase"][] = ["ready", "problem"];

/** How practice's synthetic database stands, while it's being set up too. */
export function usePracticeDatabase(enabled = true) {
  return useQuery({
    queryKey: PRACTICE_DATABASE,
    queryFn: settingsApi.practiceDatabase,
    enabled,
    // While it's starting or loading, ask again every few seconds.
    refetchInterval: (query) => (query.state.data && SETTLED.includes(query.state.data.phase) ? false : 3000),
  });
}

/**
 * Practice DataLab's own database: DataLab sets it up and starts it by
 * itself (in Docker, on this computer only), so this only says how it
 * stands, starts it again after a problem, and resets it once confirmed.
 */
export function PracticeDatabaseStatus() {
  const client = useQueryClient();
  const shown = usePracticeDatabase();
  const [confirming, setConfirming] = useState(false);
  const refresh = (data: Shown) => {
    client.setQueryData(PRACTICE_DATABASE, data);
    client.invalidateQueries({ queryKey: ["health"] });
  };
  const start = useMutation({ mutationFn: settingsApi.startPracticeDatabase, onSuccess: refresh });
  const data = shown.data;
  if (shown.isError) return <p className="mt-3 text-sm text-danger">{shown.error.message}</p>;
  if (!data) return null;
  const phase = PHASE[data.phase];
  return (
    <div className="mt-4 flex flex-col gap-2 border-l border-line pl-4 text-sm" aria-label="The practice database">
      <p className="flex flex-wrap items-center gap-2">
        <Chip tone={phase.tone}>{phase.text}</Chip>
        <span className={data.phase === "problem" ? "text-attn" : "text-muted"} role="status">
          {data.message}
        </span>
      </p>
      {data.managed && (
        <p className="text-xs text-muted">
          In Docker on this computer only (127.0.0.1, port {data.port}): the container{" "}
          <code className="font-mono">{data.container}</code>, with its data in the volume{" "}
          <code className="font-mono">{data.volume}</code>. DataLab starts it when it opens, and keeps its data when
          DataLab is updated or installed again.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {data.phase === "problem" && (
          <Button onClick={() => start.mutate()} disabled={start.isPending || data.busy}>
            Try again
          </Button>
        )}
        {data.managed && (
          <Button onClick={() => setConfirming(true)} disabled={data.busy}>
            Reset practice data…
          </Button>
        )}
      </div>
      {start.isError && <p className="text-xs text-danger">{start.error.message}</p>}
      {confirming && <ConfirmReset shown={data} onClose={() => setConfirming(false)} onDone={refresh} />}
    </div>
  );
}

function ConfirmReset({ shown, onClose, onDone }: { shown: Shown; onClose: () => void; onDone: (data: Shown) => void }) {
  const reset = useMutation({
    mutationFn: settingsApi.resetPracticeDatabase,
    onSuccess: (data) => {
      onDone(data);
      onClose();
    },
  });
  return (
    <Modal title="Reset the practice database?" onClose={onClose}>
      <div className="flex flex-col gap-3 text-sm">
        <p>
          DataLab deletes the practice database (the container and its volume) and sets it up again from scratch, with
          the same made-up data. It takes a minute or two, and practice queries can't run until it's done.
        </p>
        <p className="text-muted">
          Your practice conversations, query results and exports aren't touched. The real DataLab isn't affected.
        </p>
        {shown.cant_reset_because && (
          <p className="text-attn" role="status">
            {shown.cant_reset_because}
          </p>
        )}
        {reset.error && <p className="text-danger">{reset.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            onClick={() => reset.mutate()}
            disabled={Boolean(shown.cant_reset_because) || reset.isPending}
          >
            <Icon name="restore" size={14} /> {reset.isPending ? "Resetting…" : "Reset"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
