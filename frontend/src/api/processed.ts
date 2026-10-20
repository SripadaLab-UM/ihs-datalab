// The lab's processed data in Dropbox (backend api/processed.py): connecting
// Dropbox, choosing the folder, and its file list. Practice DataLab has its own
// synthetic folder instead.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Processed = Schemas["ProcessedOut"];
export type ProcessedSignIn = Schemas["SignInOut"];
export type ProcessedFolder = Schemas["FolderOut"];

const post: RequestInit = { method: "POST" };

export const processedApi = {
  /** Where it stands: connected, the folder chosen, and how many files are listed. */
  status: () => request<Processed>("/api/processed"),
  /** A new sign-in: Dropbox's page where the person approves the app and gets a code. */
  connect: () => request<ProcessedSignIn>("/api/processed/connect", post),
  /** Exchange the code Dropbox showed. */
  finish: (code: string) =>
    request<ProcessedSignIn>("/api/processed/connect/finish", { method: "POST", body: JSON.stringify({ code }) }),
  cancel: () => request<ProcessedSignIn>("/api/processed/connect/cancel", post),
  /** Revoke the sign-in, and forget the folder, its list and the downloaded files. */
  disconnect: () => request<ProcessedSignIn>("/api/processed/disconnect", post),
  /** The folders at the top of the team's Dropbox. */
  folders: () => request<ProcessedFolder[]>("/api/processed/folders"),
  choose: (id: string) => request<Processed>("/api/processed/folder", { method: "POST", body: JSON.stringify({ id }) }),
  refresh: () => request<Processed>("/api/processed/refresh", post),
};
