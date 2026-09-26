import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { api, type Checkpoint, type Conversation } from "@/api/client";
import { Button, Modal, Tabs } from "@/components/ui";
import { formatBytes } from "@/lib/csv";
import type { OpenFile } from "@/lib/files";

import { ExportDialog } from "./ExportDialog";
import { Inputs } from "./Inputs";

type Tab = "inputs" | "outputs" | "history" | "data";

/** The Workspace's right-hand column: what the agent made, checkpoints, and data it read. */
export function SidePanel({ conversation, onOpen }: { conversation: Conversation; onOpen: (file: OpenFile) => void }) {
  const [tab, setTab] = useState<Tab>("outputs");
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const tabs: { id: Tab; label: string }[] = [
    { id: "inputs", label: "Inputs" },
    { id: "outputs", label: "Outputs" },
    { id: "history", label: "History" },
    ...(conversation.kind === "data" ? [{ id: "data" as const, label: "Data accessed" }] : []),
  ];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "inputs" && <Inputs conversation={conversation} practice={health.data?.profile === "practice"} />}
        {tab === "outputs" && <Outputs conversation={conversation} onOpen={onOpen} />}
        {tab === "history" && <History conversation={conversation} />}
        {tab === "data" && <DataAccessed conversationId={conversation.id} onOpen={onOpen} />}
      </div>
    </div>
  );
}

const ICONS: Record<OpenFile["kind"], string> = {
  html: "📄",
  image: "🖼",
  csv: "▦",
  text: "≡",
  pdf: "📕",
  other: "•",
};

function Outputs({ conversation, onOpen }: { conversation: Conversation; onOpen: (file: OpenFile) => void }) {
  const conversationId = conversation.id;
  const files = useQuery({ queryKey: ["files", conversationId], queryFn: () => api.files(conversationId) });
  const [exporting, setExporting] = useState(false);
  if (files.data?.length === 0) {
    return (
      <p className="text-sm text-muted">
        Nothing yet. Files the agent saves in <code className="font-mono text-xs">outputs/</code> appear here after each turn.
      </p>
    );
  }
  return (
    <>
      <div className="mb-2 flex justify-end">
        <Button className="px-2 py-0.5 text-xs" onClick={() => setExporting(true)}>
          Export…
        </Button>
      </div>
      {exporting && <ExportDialog conversation={conversation} withReport={false} onClose={() => setExporting(false)} />}
      <ul className="flex flex-col gap-0.5">
        {files.data?.map((file) => (
          <li key={file.path}>
            <button
              onClick={() => onOpen({ root: "outputs", path: file.path, kind: file.kind, size: file.size })}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-sunken"
              title={file.path}
            >
              <span aria-hidden className="w-4 text-center text-muted">
                {ICONS[file.kind]}
              </span>
              <span className="min-w-0 flex-1 truncate">{file.path}</span>
              <span className="shrink-0 text-xs text-muted">{formatBytes(file.size)}</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}


function History({ conversation }: { conversation: Conversation }) {
  const checkpoints = useQuery({
    queryKey: ["checkpoints", conversation.id],
    queryFn: () => api.checkpoints(conversation.id),
  });
  const [confirming, setConfirming] = useState<Checkpoint | null>(null);
  if (checkpoints.data?.length === 0) {
    return <p className="text-sm text-muted">After each turn, DataLab saves a checkpoint of the files. They appear here.</p>;
  }
  return (
    <>
      <ol className="flex flex-col gap-2">
        {checkpoints.data?.map((checkpoint) => (
          <li key={checkpoint.number} className="rounded-lg border border-line p-2 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{checkpoint.label}</span>
              <Button
                variant="ghost"
                className="px-2 py-0.5 text-xs"
                disabled={conversation.busy}
                title={conversation.busy ? "Stop the agent first" : undefined}
                onClick={() => setConfirming(checkpoint)}
              >
                Restore
              </Button>
            </div>
            <div className="mt-0.5 text-muted">
              {new Date(checkpoint.created_at).toLocaleTimeString()} · {checkpoint.files} files · {formatBytes(checkpoint.bytes)}
            </div>
            {checkpoint.skipped.some((s) => s.reason === "too large") && (
              <div className="mt-0.5 text-research">Some large files aren't in this checkpoint.</div>
            )}
          </li>
        ))}
      </ol>
      {confirming && (
        <RestoreDialog conversationId={conversation.id} checkpoint={confirming} onClose={() => setConfirming(null)} />
      )}
    </>
  );
}

function RestoreDialog({
  conversationId,
  checkpoint,
  onClose,
}: {
  conversationId: string;
  checkpoint: Checkpoint;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const restore = useMutation({
    mutationFn: () => api.restore(conversationId, checkpoint.number),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["checkpoints", conversationId] });
      queryClient.invalidateQueries({ queryKey: ["files", conversationId] });
      onClose();
    },
  });
  const large = checkpoint.skipped.filter((s) => s.reason === "too large");
  return (
    <Modal
      title={`Restore files to “${checkpoint.label}”?`}
      onClose={onClose}
    >
      <div className="flex flex-col gap-3 text-sm">
        <p>The files in this conversation's workspace go back to how they were then. The conversation itself isn't rewound, and the agent is told on its next turn.</p>
        <p className="text-muted">DataLab saves the current files first, so you can undo this from History.</p>
        {large.length > 0 && (
          <div>
            <p className="text-research">These files were too large to checkpoint, so they stay as they are now:</p>
            <ul className="mt-1 list-disc pl-5 font-mono text-xs">
              {large.map((s) => (
                <li key={s.path}>{s.path}</li>
              ))}
            </ul>
          </div>
        )}
        {restore.error && <p className="text-danger">{restore.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => restore.mutate()} disabled={restore.isPending}>
            {restore.isPending ? "Restoring…" : "Restore files"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function DataAccessed({ conversationId, onOpen }: { conversationId: string; onOpen: (file: OpenFile) => void }) {
  const queries = useQuery({
    queryKey: ["data-accessed", conversationId],
    queryFn: () => api.dataAccessed(conversationId),
    refetchInterval: 3000,
  });
  if (queries.data?.length === 0) return <p className="text-sm text-muted">No queries yet.</p>;
  return (
    <ol className="flex flex-col gap-2">
      {queries.data?.map((q) => (
        <li key={q.id} className="rounded-lg border border-line p-2 text-xs">
          <div className="flex justify-between">
            <span className={clsx(q.status === "succeeded" ? "text-data" : q.status === "running" ? "text-accent" : "text-danger")}>
              {q.status}
            </span>
            <span className="text-muted">
              {q.row_count != null && `${q.row_count.toLocaleString()} rows · `}
              {new Date(q.started_at).toLocaleTimeString()}
            </span>
          </div>
          <div className="mt-1 font-medium">{q.tables.join(", ") || "—"}</div>
          <details className="mt-1">
            <summary className="cursor-pointer text-muted">SQL</summary>
            <pre className="mt-1 whitespace-pre-wrap font-mono">{q.sql_text}</pre>
          </details>
          {q.result_file && q.status === "succeeded" && (
            <button
              className="mt-1 text-accent underline"
              onClick={() => onOpen({ root: "results", path: q.result_file!, kind: "csv" })}
            >
              View result
            </button>
          )}
          {q.message && <p className="mt-1 text-muted">{q.message}</p>}
        </li>
      ))}
    </ol>
  );
}
