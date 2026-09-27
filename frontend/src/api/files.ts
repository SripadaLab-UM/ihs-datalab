// A conversation's workspace files: outputs, inputs attached to it, and the
// checkpoints its files can be restored to.
import { ApiError, request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type WorkspaceFile = Schemas["FileOut"];
export type FileRoot = Schemas["NewPreview"]["root"];
export type Checkpoint = Schemas["CheckpointOut"];
export type RestoreResult = Schemas["RestoreOut"];
export type Attachment = Schemas["AttachmentOut"];
export type AttachResult = Schemas["AttachResult"];

/** Where the viewer loads a file from (images are shown straight from this), as of one checkpoint. */
const fileUrl = (id: string, root: FileRoot, path: string, checkpoint?: number | null) =>
  `/api/conversations/${id}/files/${root}/${path.split("/").map(encodeURIComponent).join("/")}` +
  (checkpoint != null ? `?checkpoint=${checkpoint}` : "");

export const filesApi = {
  files: (id: string, root: FileRoot = "outputs") =>
    request<WorkspaceFile[]>(`/api/conversations/${id}/files?root=${root}`),
  fileUrl,
  fileText: async (id: string, root: FileRoot, path: string, checkpoint?: number | null) => {
    const response = await fetch(fileUrl(id, root, path, checkpoint));
    if (!response.ok) throw new ApiError(response.status, "This file can't be shown.");
    return { text: await response.text(), truncated: response.headers.get("x-datalab-truncated") === "1" };
  },
  preview: (id: string, root: FileRoot, path: string, checkpoint?: number | null) =>
    request<{ url: string; checkpoint: number }>(`/api/conversations/${id}/previews`, {
      method: "POST",
      body: JSON.stringify({ root, path, checkpoint: checkpoint ?? null }),
    }),
  checkpoints: (id: string) => request<Checkpoint[]>(`/api/conversations/${id}/checkpoints`),
  restore: (id: string, number: number) =>
    request<RestoreResult>(`/api/conversations/${id}/checkpoints/${number}/restore`, { method: "POST" }),
  inputs: (id: string) => request<Attachment[]>(`/api/conversations/${id}/inputs`),
  inputSamples: () => request<string[]>("/api/input-samples"),
  attach: (id: string, source: "files" | "folder" | "sample", sample?: string) =>
    request<AttachResult>(`/api/conversations/${id}/inputs`, {
      method: "POST",
      body: JSON.stringify({ source, sample }),
    }),
  detach: (id: string, attachmentId: string) =>
    request<void>(`/api/conversations/${id}/inputs/${attachmentId}`, { method: "DELETE" }),
};
