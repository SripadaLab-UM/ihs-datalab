// The Workflows tab's live parts: following a run as it goes.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { type RunDetail, runStreamUrl, workflowsApi } from "@/api/workflows";

import { isLive } from "./words";

export const runKey = (runId: string) => ["workflow-run", runId] as const;

/**
 * A run, kept up to date while it goes. The server sends the whole run (with
 * its steps) each time it changes, and `end` once it has finished; the
 * browser's EventSource reconnects by itself if the connection drops, and
 * each snapshot replaces the last, so nothing can be missed or doubled.
 */
export function useRun(runId: string | undefined) {
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: runKey(runId ?? ""),
    queryFn: () => workflowsApi.run(runId!),
    enabled: Boolean(runId),
  });
  const live = run.data ? isLive(run.data) : false;

  useEffect(() => {
    if (!runId || !live || typeof EventSource === "undefined") return;
    const source = new EventSource(runStreamUrl(runId));
    const finished = () => {
      // The lists show the run's outcome now.
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
    };
    const onRun = (message: MessageEvent<string>) => {
      const snapshot = JSON.parse(message.data) as RunDetail;
      queryClient.setQueryData(runKey(runId), snapshot);
      if (!isLive(snapshot)) finished();
    };
    const onEnd = () => {
      source.close();
      finished();
    };
    source.addEventListener("run", onRun);
    source.addEventListener("end", onEnd);
    return () => source.close();
  }, [runId, live, queryClient]);

  return run;
}
