// Docker on Windows (backend api/docker.py): how it stands, opening Docker
// Desktop, and putting back the right its virtual machine needs, behind one
// Windows administrator prompt.
import { request } from "./http";
import type { components } from "./schema";

export type DockerStatus = components["schemas"]["DockerStatusOut"];
export type DockerFix = components["schemas"]["DockerFixOut"];
export type DockerState = DockerStatus["state"];

export const dockerApi = {
  /** The last check's answer (a new one runs at most every 15 seconds). */
  status: () => request<DockerStatus>("/api/docker"),
  /** Checks again now: the dialog's Check again. */
  check: () => request<DockerStatus>("/api/docker/check", { method: "POST", body: "{}" }),
  start: () => request<DockerStatus>("/api/docker/start", { method: "POST", body: "{}" }),
  /** Answers once the person has answered Windows' permission box. */
  fix: () => request<DockerFix>("/api/docker/fix", { method: "POST", body: "{}" }),
};
