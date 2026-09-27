// Provenance: how a workspace file was made (backend api/provenance.py). The
// numbers' sources come with the conversation's events, as `provenance` events.
import { request } from "./http";
import type { components } from "./schema";

export type FileProvenance = components["schemas"]["FileProvenanceOut"];

export const provenanceApi = {
  /** How a file in /work (such as "outputs/fig1.png") came to be as `checkpoint` (else the latest) saved it. */
  file: (id: string, path: string, checkpoint?: number | null) =>
    request<FileProvenance>(
      `/api/conversations/${id}/provenance/${path.split("/").map(encodeURIComponent).join("/")}` +
        (checkpoint != null ? `?checkpoint=${checkpoint}` : ""),
    ),
};
