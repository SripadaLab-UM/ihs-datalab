// Whether this is the lab's shared DataLab (a server) and, there, whether the
// person signed in is one of its administrators. A person's own computer is
// theirs: everything is theirs to see and change.
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { sessionApi } from "@/api/session";

export function useDeployment(): { server: boolean; admin: boolean } {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const server = health.data?.deployment === "server";
  const me = useQuery({ queryKey: ["session-me"], queryFn: sessionApi.me, staleTime: 5 * 60_000, enabled: server });
  return { server, admin: !server || (me.data?.admin ?? false) };
}
