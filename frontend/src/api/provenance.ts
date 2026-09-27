// Provenance: how a workspace file was made (backend api/provenance.py). The
// numbers' sources come with the conversation's events, as `provenance` events.
import { request } from "./http";
import type { components } from "./schema";

export type FileProvenance = components["schemas"]["FileProvenanceOut"];

export const provenanceApi = {
  /** How a file in /work (such as "outputs/fig1.png") came to be as its latest checkpoint saved it. */
  file: (id: string, path: string) =>
    request<FileProvenance>(`/api/conversations/${id}/provenance/${path.split("/").map(encodeURIComponent).join("/")}`),
};
