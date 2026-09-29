// Docker on Windows (backend api/docker.py): how it stands, opening Docker
// Desktop, and putting back the right its virtual machine needs, behind one
// Windows administrator prompt.
import { request } from "./http";
import type { components } from "./schema";

export type DockerStatus = components["schemas"]["DockerStatusOut"];
export type DockerFix = components["schemas"]["DockerFixOut"];
export type DockerState = DockerStatus["state"];

export const dockerApi = {
  /** With `fresh`, checks again now rather than using the last few seconds' answer. */
  status: (fresh = false) => request<DockerStatus>(`/api/docker${fresh ? "?fresh=true" : ""}`),
  start: () => request<DockerStatus>("/api/docker/start", { method: "POST", body: "{}" }),
  /** Answers once the person has answered Windows' permission box. */
  fix: () => request<DockerFix>("/api/docker/fix", { method: "POST", body: "{}" }),
};
