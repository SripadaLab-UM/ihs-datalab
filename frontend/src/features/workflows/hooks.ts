// The Workflows tab's live parts: following a run as it goes.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { type RunDetail, runStreamUrl, workflowsApi } from "@/api/workflows";

import { isLive } from "./words";

const POLL_MS = 2000;

export const runKey = (runId: string) => ["workflow-run", runId] as const;

/**
 * A run, kept up to date while it goes. The server sends the whole run (with
 * its steps) each time it changes, and `end` once it has finished; the
 * browser's EventSource reconnects by itself if the connection drops, and
 * each snapshot replaces the last, so nothing can be missed or doubled.
 * If the browser gives up on it (DataLab answered 401 or 404, which it won't
 * retry), the run is fetched every few seconds instead until it has finished.
 */
export function useRun(runId: string | undefined) {
  const queryClient = useQueryClient();
  const [lost, setLost] = useState(false);
  const run = useQuery({
    queryKey: runKey(runId ?? ""),
    queryFn: () => workflowsApi.run(runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => (lost && query.state.data && isLive(query.state.data) ? POLL_MS : false),
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
    const onError = () => {
      // Closed for good (not just reconnecting): ask for the run instead.
      if (source.readyState !== EventSource.CLOSED) return;
      setLost(true);
      queryClient.invalidateQueries({ queryKey: runKey(runId) });
    };
    source.addEventListener("run", onRun);
    source.addEventListener("end", onEnd);
    source.addEventListener("error", onError);
    return () => source.close();
  }, [runId, live, queryClient]);

  return run;
}
