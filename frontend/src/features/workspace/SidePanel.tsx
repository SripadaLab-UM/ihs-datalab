import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { api, type Checkpoint, type Conversation, type WorkspaceFile } from "@/api/client";
import { shortTable } from "@/components/chat/activity";
import { Button, Chip, EmptyNote, FileGlyph, Icon, Modal, Tabs } from "@/components/ui";
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
    { id: "outputs", label: "Outputs" },
    { id: "inputs", label: "Inputs" },
    ...(conversation.kind === "data" ? [{ id: "data" as const, label: "Queries" }] : []),
    { id: "history", label: "History" },
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

function splitPath(path: string) {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? { dir: "", name: path } : { dir: path.slice(0, cut + 1), name: path.slice(cut + 1) };
}

function Outputs({ conversation, onOpen }: { conversation: Conversation; onOpen: (file: OpenFile) => void }) {
  const conversationId = conversation.id;
  const files = useQuery({ queryKey: ["files", conversationId], queryFn: () => api.files(conversationId) });
  const [exporting, setExporting] = useState(false);
  if (files.data?.length === 0) {
    return (
      <EmptyNote icon="folder" title="Nothing made yet">
        Charts, tables and reports the agent saves in <code className="font-mono">outputs/</code> show up here after
        each turn.
      </EmptyNote>
    );
  }
  const all = files.data ?? [];
  const figures = all.filter((f) => f.kind === "image");
  const others = all.filter((f) => f.kind !== "image");
  const open = (file: WorkspaceFile) =>
    onOpen({ root: "outputs", path: file.path, kind: file.kind, size: file.size, checkpoint: file.checkpoint });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">
          {all.length} file{all.length === 1 ? "" : "s"}
        </span>
        <Button className="px-3 py-1 text-xs" onClick={() => setExporting(true)}>
          <Icon name="export" size={14} /> Export…
        </Button>
      </div>
      {exporting && <ExportDialog conversation={conversation} withReport={false} onClose={() => setExporting(false)} />}
      {figures.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">Figures</h3>
          <ul className="grid grid-cols-2 gap-2">
            {figures.map((file) => (
              <li key={file.path}>
                <button
                  onClick={() => open(file)}
                  title={file.path}
                  className="group flex w-full flex-col overflow-hidden rounded-xl border border-line bg-surface text-left hover:border-accent/50"
                >
                  <span className="flex aspect-[4/3] w-full items-center justify-center bg-sunken">
                    <img
                      src={api.fileUrl(conversationId, "outputs", file.path, file.checkpoint)}
                      alt=""
                      loading="lazy"
                      className="max-h-full max-w-full object-contain"
                    />
                  </span>
                  <span className="truncate px-2 py-1.5 text-xs group-hover:text-accent">{splitPath(file.path).name}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {others.length > 0 && (
        <section>
          {figures.length > 0 && (
            <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">Files</h3>
          )}
          <ul className="flex flex-col gap-0.5">
            {others.map((file) => {
              const { dir, name } = splitPath(file.path);
              return (
                <li key={file.path}>
                  <button
                    onClick={() => open(file)}
                    className="flex w-full items-center gap-2.5 rounded-lg px-1.5 py-1.5 text-left hover:bg-sunken"
                    title={file.path}
                  >
                    <FileGlyph kind={file.kind} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm">{name}</span>
                      <span className="block truncate text-xs text-muted">
                        {dir && <span className="font-mono">{dir} · </span>}
                        {formatBytes(file.size)}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

function History({ conversation }: { conversation: Conversation }) {
  const checkpoints = useQuery({
    queryKey: ["checkpoints", conversation.id],
    queryFn: () => api.checkpoints(conversation.id),
  });
  const [confirming, setConfirming] = useState<Checkpoint | null>(null);
  if (checkpoints.data?.length === 0) {
    return (
      <EmptyNote icon="history" title="No checkpoints yet">
        After each turn DataLab saves a copy of the workspace files, so you can always go back.
      </EmptyNote>
    );
  }
  return (
    <>
      <p className="mb-3 text-xs text-muted">A copy of the files after each turn. Restoring never loses the current ones.</p>
      <ol className="relative flex flex-col gap-3 before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-px before:bg-line">
        {checkpoints.data?.map((checkpoint, index) => (
          <li key={checkpoint.number} className="relative flex gap-3 pl-0">
            <span
              aria-hidden
              className={clsx(
                "relative z-[1] mt-1 h-[15px] w-[15px] shrink-0 rounded-full border-2 bg-surface",
                index === 0 ? "border-accent" : "border-line",
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-2">
                <span className="text-sm font-medium">{checkpoint.label}</span>
                <Button
                  variant="ghost"
                  className="shrink-0 px-2 py-0.5 text-xs"
                  disabled={conversation.busy}
                  title={conversation.busy ? "Stop the agent first" : "Put the files back as they were here"}
                  onClick={() => setConfirming(checkpoint)}
                >
                  <Icon name="restore" size={13} /> Restore
                </Button>
              </div>
              <div className="text-xs text-muted">
                {new Date(checkpoint.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} ·{" "}
                {checkpoint.files} file{checkpoint.files === 1 ? "" : "s"} · {formatBytes(checkpoint.bytes)}
              </div>
              {checkpoint.skipped.some((s) => s.reason === "too large") && (
                <div className="mt-0.5 text-xs text-research">Some large files aren't in this checkpoint.</div>
              )}
            </div>
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
  if (queries.data?.length === 0) {
    return (
      <EmptyNote icon="db" title="No queries yet">
        Every read from the study database is listed here: which tables, how many rows, and the exact SQL.
      </EmptyNote>
    );
  }
  return (
    <>
      <p className="mb-3 flex items-center gap-1.5 text-xs text-muted">
        <Icon name="lock" size={13} /> Read-only. Every query the agent ran, newest last.
      </p>
      <ol className="flex flex-col gap-2">
        {queries.data?.map((q) => (
          <li key={q.id} className="rounded-xl border border-line bg-surface p-3 text-xs">
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className={clsx(
                  "h-2 w-2 shrink-0 rounded-full",
                  q.status === "succeeded" ? "bg-accent" : q.status === "running" ? "dl-breathe bg-attn" : "bg-danger",
                )}
              />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {q.tables.map(shortTable).join(", ") || (q.status === "rejected" ? "Stopped before it ran" : "No tables named")}
              </span>
              <span className="shrink-0 text-muted">
                {new Date(q.started_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {q.status === "succeeded" && q.row_count != null && (
                <Chip tone="good">
                  {q.row_count.toLocaleString()} row{q.row_count === 1 ? "" : "s"}
                </Chip>
              )}
              {q.status === "running" && <Chip tone="attn">running</Chip>}
              {q.status !== "succeeded" && q.status !== "running" && <Chip tone="bad">{q.status}</Chip>}
            </div>
            {q.message && <p className="mt-2 text-muted">{q.message}</p>}
            <details className="group mt-2">
              <summary className="flex cursor-pointer list-none items-center gap-1 text-muted hover:text-ink">
                <Icon name="chevron" size={12} className="transition-transform group-open:rotate-90" /> The SQL
              </summary>
              <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded-lg bg-sunken p-2 font-mono text-[11.5px] leading-relaxed">
                {q.sql_text}
              </pre>
            </details>
            {q.result_file && q.status === "succeeded" && (
              <button
                className="mt-2 inline-flex items-center gap-1 font-medium text-accent hover:underline"
                onClick={() => onOpen({ root: "results", path: q.result_file!, kind: "csv" })}
              >
                <Icon name="open" size={13} /> Open the result
              </button>
            )}
          </li>
        ))}
      </ol>
    </>
  );
}
