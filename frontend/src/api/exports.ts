// Export folders, and exporting a conversation's files (and its report) to one.
import type { FileRoot } from "./files";
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Destination = Schemas["DestinationOut"];
export type ExportResult = Schemas["ExportOut"];
export type DestinationPlaces = Schemas["PlacesOut"];
export type DestinationPlace = Schemas["PlaceOut"];
export type FolderTest = Schemas["FolderTestOut"];

export const exportsApi = {
  destinations: () => request<Destination[]>("/api/export-destinations"),
  /** Sync apps' folders found on this computer (by name), for the picker to open in. */
  destinationPlaces: () => request<DestinationPlaces>("/api/export-destinations/places"),
  /** Opens the computer's folder picker (in `startIn`, a place's id); the person chooses. */
  addDestination: (options: { name?: string; startIn?: string } = {}) =>
    request<Destination>("/api/export-destinations", {
      method: "POST",
      body: JSON.stringify({ name: options.name || null, start_in: options.startIn ?? null }),
    }),
  changeDestination: (id: string, change: { name?: string; offered?: boolean }) =>
    request<Destination>(`/api/export-destinations/${id}`, { method: "PATCH", body: JSON.stringify(change) }),
  /** Saves a small synthetic file there, reads it back, and removes it. */
  testDestination: (id: string) => request<FolderTest>(`/api/export-destinations/${id}/test`, { method: "POST" }),
  /** Forgets the folder; it and everything in it stay. */
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
