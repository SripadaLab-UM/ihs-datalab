import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/api/client";

/** After the knowledge base synced, DataLab reads its table catalog again (backend
 *  catalog_source.py): everything showing the catalog asks again. */
export function refreshCatalog(queryClient: QueryClient) {
  for (const key of ["health", "catalog-status", "sql-catalog", "sql-catalog-search"])
    void queryClient.invalidateQueries({ queryKey: [key] });
}

// How often, and for how long after the page opens, to look for the catalog.
export const CATALOG_CHECK_MS = 3_000;
export const CATALOG_WAIT_MS = 2 * 60_000;

/**
 * The lab's shared DataLab pulls the knowledge base and pipelines repo in the
 * background after each GitHub sign-in (backend repos/sign_in_sync.py), so the
 * first page can open before the catalog is there. While it isn't, ask again
 * every few seconds, for a couple of minutes; once it comes, everything showing
 * the catalog or the repos' state asks again, with no reload.
 */
export function useCatalogAfterSignIn() {
  const queryClient = useQueryClient();
  const opened = useRef(Date.now());
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: (query) => {
      const data = query.state.data;
      const waiting = data?.deployment === "server" && data.catalog_state === "empty";
      return waiting && Date.now() - opened.current < CATALOG_WAIT_MS ? CATALOG_CHECK_MS : false;
    },
  });
  const state = health.data?.catalog_state;
  const before = useRef(state);
  useEffect(() => {
    if (before.current === "empty" && state && state !== "empty") {
      refreshCatalog(queryClient);
      for (const key of ["knowledge-status", "pipelines-status"]) void queryClient.invalidateQueries({ queryKey: [key] });
    }
    before.current = state;
  }, [state, queryClient]);
}
