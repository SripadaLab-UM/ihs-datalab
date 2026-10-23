// Uploading files into a conversation on the lab's server, where the
// browser, not DataLab, reaches the person's own disk. One request per file,
// one after another; DataLab refuses any it can't take (a credentials file,
// an odd name, too large) and the rest go on.
import { api, ApiError, type AttachResult } from "@/api/client";

export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
  file: string;
}

/** A file's path inside the upload: its name, or `<folder>/<path inside it>`. */
export function uploadPath(file: File, kind: "files" | "folder"): string {
  return kind === "folder" && file.webkitRelativePath ? file.webkitRelativePath : file.name;
}

export async function uploadAndAttach(
  conversationId: string,
  files: File[],
  kind: "files" | "folder",
  onProgress: (progress: UploadProgress) => void,
): Promise<AttachResult> {
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  const upload = await api.startUpload(conversationId, kind);
  if (totalBytes > upload.room_bytes) {
    throw new Error(
      `That's ${megabytes(totalBytes)}; this conversation has room for ${megabytes(upload.room_bytes)} more. Remove some uploads first.`,
    );
  }
  const refused: AttachResult["refused"] = [];
  let sentBytes = 0;
  for (const file of files) {
    const path = uploadPath(file, kind);
    onProgress({ sentBytes, totalBytes, file: path });
    if (file.size > upload.max_file_bytes) {
      refused.push({
        path,
        reason: `${path} is larger than ${megabytes(upload.max_file_bytes)}.`,
      });
    } else {
      try {
        await api.uploadFile(conversationId, upload.id, path, file);
      } catch (error) {
        if (!(error instanceof ApiError) || (error.status !== 413 && error.status !== 422)) throw error;
        refused.push({ path, reason: error.message });
      }
    }
    sentBytes += file.size;
  }
  onProgress({ sentBytes, totalBytes, file: "" });
  if (refused.length === files.length) return { added: [], refused, warnings: [] };
  const attached = await api.attachUpload(conversationId, upload.id);
  return { ...attached, refused: [...refused, ...attached.refused] };
}

function megabytes(bytes: number): string {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.ceil(bytes / 1024 ** 2)} MB`;
}
