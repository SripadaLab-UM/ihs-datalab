// Send feedback's support reports (backend api/support.py, docs/SUPPORT.md):
// preview a report, save it, reopen it, save it to a folder to email, or send
// it to the lab's private support repository.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type SupportDraft = Schemas["SupportDraftIn"];
export type SupportAttachment = Schemas["SupportAttachmentIn"];
export type SupportPreview = Schemas["SupportPreviewOut"];
export type SupportContents = Schemas["SupportContentsOut"];
export type SupportReport = Schemas["SupportReportOut"];
export type SupportReportDetail = Schemas["SupportReportDetailOut"];
export type SupportState = SupportReport["states"][number];
export type SupportStatus = Schemas["SupportStatusOut"];
export type SupportRepo = Schemas["SupportRepoOut"];
export type SupportFolderCopy = Schemas["SupportFolderCopyOut"];

const report = (id: string) => `/api/support/reports/${encodeURIComponent(id)}`;

export const supportApi = {
  status: () => request<SupportStatus>("/api/support/status"),
  /** With `check`, asks GitHub whether the support repository is private and writable. */
  github: (check = false) => request<SupportRepo>(`/api/support/github${check ? "?check=true" : ""}`),
  /** Builds the bundle without saving it: exactly what would be saved. */
  preview: (draft: SupportDraft) =>
    request<SupportPreview>("/api/support/preview", { method: "POST", body: JSON.stringify(draft) }),
  /** Saves the previewed draft, as it was shown. */
  save: (draftId: string) =>
    request<SupportReportDetail>("/api/support/reports", { method: "POST", body: JSON.stringify({ draft_id: draftId }) }),
  reports: () => request<SupportReport[]>("/api/support/reports"),
  report: (id: string) => request<SupportReportDetail>(report(id)),
  /** The ZIP, as a download (a link to this, in the person's own browser). */
  bundleUrl: (id: string) => `${report(id)}/bundle`,
  remove: (id: string) => request<void>(report(id), { method: "DELETE" }),
  saveToFolder: (id: string, destinationId: string) =>
    request<SupportReport>(`${report(id)}/save-to-folder`, {
      method: "POST",
      body: JSON.stringify({ destination_id: destinationId }),
    }),
  /** Sends it to the lab's support repository (and retries). The person saw where it goes. */
  send: (id: string) =>
    request<SupportReport>(`${report(id)}/send`, { method: "POST", body: JSON.stringify({ confirmed: true }) }),
};
