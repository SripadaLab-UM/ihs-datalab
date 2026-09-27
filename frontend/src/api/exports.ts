// Export folders, and exporting a conversation's files (and its report) to one.
import type { FileRoot } from "./files";
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Destination = Schemas["DestinationOut"];
export type ExportResult = Schemas["ExportOut"];

export const exportsApi = {
  destinations: () => request<Destination[]>("/api/export-destinations"),
  addDestination: () => request<Destination>("/api/export-destinations", { method: "POST" }),
  removeDestination: (id: string) => request<void>(`/api/export-destinations/${id}`, { method: "DELETE" }),
  export: (
    id: string,
    destinationId: string,
    files: { root: FileRoot; path: string }[],
    report?: { html: string; css: string },
    checkpoint?: number,
    rawHtml = false,
  ) =>
    request<ExportResult>(`/api/conversations/${id}/exports`, {
      method: "POST",
      body: JSON.stringify({ destination_id: destinationId, files, report, checkpoint, raw_html: rawHtml }),
    }),
};
