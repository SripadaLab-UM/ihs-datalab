import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, type Conversation } from "@/api/client";
import { Button, EmptyNote, FileGlyph, Icon } from "@/components/ui";
import { kindOf } from "@/lib/files";
import { pickerOpenHint } from "@/lib/picker";
import { uploadAndAttach, type UploadProgress } from "@/lib/upload";

/** Files and folders attached to a conversation. The agent reads them; it can't change them. */
export function Inputs({ conversation, practice }: { conversation: Conversation; practice: boolean }) {
  const queryClient = useQueryClient();
  const inputs = useQuery({
    queryKey: ["inputs", conversation.id],
    queryFn: () => api.inputs(conversation.id),
  });
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  // The lab's server: files come from the browser, not DataLab's own disk.
  const server = health.data?.deployment === "server";
  const mode = modes.data?.find((m) => m.id === conversation.mode);
  const [refused, setRefused] = useState<{ path: string; reason: string }[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["inputs", conversation.id] });
  const attach = useMutation({
    mutationFn: async (request: { source: "files" | "folder" | "sample"; sample?: string }) => {
      if (
        conversation.kind === "research" &&
        !window.confirm(
          "This is a research session, which has the internet. Anything you attach may be sent to websites. Attach anyway?",
        )
      ) {
        return { added: [], refused: [], warnings: [] };
      }
      return api.attach(conversation.id, request.source, request.sample);
    },
    onSuccess: (result) => {
      setRefused(result.refused);
      setWarnings(result.warnings ?? []);
      refresh();
    },
  });
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const upload = useMutation({
    mutationFn: async ({ files, kind }: { files: File[]; kind: "files" | "folder" }) => {
      if (
        conversation.kind === "research" &&
        !window.confirm(
          "This is a research session, which has the internet. Anything you upload may be sent to websites. Upload anyway?",
        )
      ) {
        return { added: [], refused: [], warnings: [] };
      }
      return uploadAndAttach(conversation.id, files, kind, setProgress);
    },
    onSuccess: (result) => {
      setRefused(result.refused);
      setWarnings(result.warnings ?? []);
      refresh();
    },
    onSettled: () => setProgress(null),
  });
  const detach = useMutation({
    mutationFn: (id: string) => api.detach(conversation.id, id),
    onSuccess: refresh,
  });
  const blocked = conversation.busy || attach.isPending || upload.isPending;

  if (mode && !mode.attachments) {
    // The tabs' own chats (Knowledge writing, Workflow authoring, ...): DataLab refuses to attach, too.
    return (
      <EmptyNote icon="attach" title="Nothing can be attached here">
        {mode.queries
          ? `${mode.label} works from the database and its tab's own files, so files can't be attached. Attach files in an Analysis, Data extraction or Data engineering conversation.`
          : `${mode.label} works with the database catalog and the knowledge base only, so files can't be attached. Send the page with your message, or attach files in another mode.`}
      </EmptyNote>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2.5 font-sans text-[13px] leading-relaxed text-muted">
        <p>
          The agent can read these at <code className="font-mono text-ink">/inputs</code> but can't change them. An
          attached folder is shared whole, including anything added to it later.
          {conversation.kind === "research" && (
            <span className="mt-1 block text-research">
              This is a research session: anything attached may reach the internet.
            </span>
          )}
        </p>
      </div>
      {server && !practice && (
        <Uploader disabled={blocked} onFiles={(files, kind) => upload.mutate({ files, kind })} />
      )}
      {progress && (
        <p className="text-xs text-muted" role="status">
          Uploading{progress.file ? ` ${progress.file}` : ""}…{" "}
          {progress.totalBytes > 0 && `${Math.floor((100 * progress.sentBytes) / progress.totalBytes)}%`}
        </p>
      )}
      {upload.error && <p className="text-xs text-danger">{upload.error.message}</p>}
      {practice ? (
        <Samples onAttach={(sample) => attach.mutate({ source: "sample", sample })} disabled={blocked} />
      ) : server ? null : (
        <div className="flex gap-2">
          <Button onClick={() => attach.mutate({ source: "files" })} disabled={blocked}>
            <Icon name="attach" size={14} /> Attach files…
          </Button>
          <Button onClick={() => attach.mutate({ source: "folder" })} disabled={blocked}>
            <Icon name="folder" size={14} /> Attach folder…
          </Button>
        </div>
      )}
      {attach.isPending && !practice && attach.variables?.source !== "sample" && (
        <p className="text-xs text-attn" role="status">
          {pickerOpenHint(attach.variables?.source === "folder" ? "folder" : "files")}
        </p>
      )}
      {conversation.busy && <p className="text-xs text-muted">You can attach once the agent has finished.</p>}
      {attach.error && <p className="text-xs text-danger">{attach.error.message}</p>}
      {detach.error && <p className="text-xs text-danger">{detach.error.message}</p>}
      {warnings.map((warning) => (
        <p key={warning} className="rounded-lg bg-research-soft px-2 py-1 text-xs text-research" role="alert">
          {warning}
        </p>
      ))}
      {refused.map((r) => (
        <p key={r.path} className="text-xs text-danger">
          Not attached: {r.reason}
        </p>
      ))}
      {inputs.data?.length === 0 && (
        <EmptyNote icon="attach" title="Nothing attached">
          Attach a file or folder when the agent needs something that isn't in the study database.
        </EmptyNote>
      )}
      <ul className="flex flex-col border-t border-line empty:border-0">
        {inputs.data?.map((item) => (
          <li key={item.id} className="flex items-center gap-3 border-b border-line py-2.5">
            <FileGlyph kind={item.kind === "folder" ? "folder" : kindOf(item.container_path)} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-mono text-xs" title={item.container_path}>
                {item.container_path}
              </div>
              <div className="truncate text-xs text-muted" title={server ? undefined : item.host_path}>
                {practice ? "Practice sample" : server ? "Uploaded" : item.host_path}
              </div>
              {!item.available && (
                <div className="text-xs text-danger">
                  {server ? "Its files are gone." : "Not found on this computer any more."}
                </div>
              )}
            </div>
            <Button
              variant="ghost"
              className="shrink-0 px-2 py-0.5 text-xs"
              disabled={conversation.busy || detach.isPending}
              onClick={() => detach.mutate(item.id)}
            >
              Remove
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The lab's server: choose files or a folder in the browser; they're uploaded, then attached. */
function Uploader({
  disabled,
  onFiles,
}: {
  disabled: boolean;
  onFiles: (files: File[], kind: "files" | "folder") => void;
}) {
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const chosen = (kind: "files" | "folder") => (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // the same files can be chosen again
    if (files.length) onFiles(files, kind);
  };
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-2">
        <Button onClick={() => filesInput.current?.click()} disabled={disabled}>
          <Icon name="attach" size={14} /> Upload files…
        </Button>
        <Button onClick={() => folderInput.current?.click()} disabled={disabled}>
          <Icon name="folder" size={14} /> Upload folder…
        </Button>
      </div>
      <input ref={filesInput} type="file" multiple hidden onChange={chosen("files")} data-testid="upload-files" />
      <input
        ref={folderInput}
        type="file"
        hidden
        onChange={chosen("folder")}
        data-testid="upload-folder"
        {...{ webkitdirectory: "" }}
      />
      <p className="text-xs text-muted">
        Up to 500 MB a file and 2 GB a conversation. They're kept with this conversation on the lab's DataLab until you
        remove them.
      </p>
    </div>
  );
}

function Samples({ onAttach, disabled }: { onAttach: (sample: string) => void; disabled: boolean }) {
  const samples = useQuery({
    queryKey: ["input-samples"],
    queryFn: api.inputSamples,
  });
  return (
    <div>
      <p className="dl-label mb-2">Synthetic samples (practice can't attach your files)</p>
      <ul className="flex flex-col border-t border-line">
        {samples.data?.map((name) => (
          <li key={name} className="border-b border-line">
            <button
              disabled={disabled}
              onClick={() => onAttach(name)}
              className="group flex w-full items-center gap-3 py-2 text-left text-xs"
            >
              <FileGlyph kind={name.includes(".") ? kindOf(name) : "folder"} size={26} />
              <span className="min-w-0 flex-1 truncate font-mono">{name}</span>
              <span className="shrink-0 font-sans text-[12.5px] text-ink underline decoration-faint underline-offset-4 group-enabled:group-hover:decoration-ink">
                Attach
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
