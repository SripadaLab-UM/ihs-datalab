import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";

import { api, type Checkpoint, type Conversation, type WorkspaceFile } from "@/api/client";
import { shortTable } from "@/components/chat/activity";
import { SHOW_QUERY } from "@/components/chat/provenance";
import { Button, Chip, EmptyNote, FileGlyph, Icon, InfoTip, Modal, Tabs } from "@/components/ui";
import { SaveAsWorkflow } from "@/features/workflows/SaveAsWorkflow";
import { formatBytes } from "@/lib/csv";
import type { OpenFile } from "@/lib/files";

import { ExportDialog } from "./ExportDialog";
import { Inputs } from "./Inputs";

type Tab = "inputs" | "outputs" | "history" | "data";

/** The Workspace's right-hand column: what the agent made, checkpoints, and data it read. */
export function SidePanel({ conversation, onOpen }: { conversation: Conversation; onOpen: (file: OpenFile) => void }) {
  const [tab, setTab] = useState<Tab>("outputs");
  // A query to show, when a number's sources or "How was this made?" asks for
  // one: each ask is new (the same query again scrolls to it again).
  const [shown, setShown] = useState<{ id: string } | null>(null);
  useEffect(() => {
    const show = (event: Event) => {
      setTab("data");
      setShown({ id: String((event as CustomEvent<{ id: string }>).detail?.id ?? "") });
    };
    window.addEventListener(SHOW_QUERY, show);
    return () => window.removeEventListener(SHOW_QUERY, show);
  }, []);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const tabs: { id: Tab; label: string }[] = [
    { id: "outputs", label: "Outputs" },
    { id: "inputs", label: "Inputs" },
    ...(conversation.kind === "data" ? [{ id: "data" as const, label: "Queries" }] : []),
    { id: "history", label: "History" },
  ];
  return (
    <div data-tour="outputs" className="flex h-full min-h-0 flex-col">
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {tab === "inputs" && <Inputs conversation={conversation} practice={health.data?.profile === "practice"} />}
        {tab === "outputs" && <Outputs conversation={conversation} onOpen={onOpen} />}
        {tab === "history" && <History conversation={conversation} />}
        {tab === "data" && <DataAccessed conversationId={conversation.id} onOpen={onOpen} shown={shown} />}
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
  // Grouped by kind of file, not by a guess at which one is the result.
  const groups: { title: string; files: WorkspaceFile[] }[] = [
    { title: "Documents", files: all.filter((f) => f.kind === "html" || f.kind === "pdf") },
    { title: "Figures", files: all.filter((f) => f.kind === "image") },
    { title: "Tables", files: all.filter((f) => f.kind === "csv") },
    { title: "Other files", files: all.filter((f) => !["html", "pdf", "image", "csv"].includes(f.kind)) },
  ].filter((group) => group.files.length > 0);
  const prefix = sharedPrefix(all.map((f) => splitPath(f.path).name));
  const open = (file: WorkspaceFile) =>
    onOpen({ root: "outputs", path: file.path, kind: file.kind, size: file.size, checkpoint: file.checkpoint });
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <span className="dl-label">
          {all.length} file{all.length === 1 ? "" : "s"} in outputs
        </span>
        <span className="flex items-center gap-1">
          <Button variant="ghost" className="px-0 py-0 text-xs" onClick={() => setExporting(true)}>
            <Icon name="export" size={14} /> Export…
          </Button>
          <InfoTip term="export" align="end" />
        </span>
      </div>
      {prefix && (
        <p className="-mt-3 font-sans text-[12px] text-muted">
          All start <span className="font-mono text-ink">{prefix}</span>; shown without it.
        </p>
      )}
      {exporting && <ExportDialog conversation={conversation} withReport={false} onClose={() => setExporting(false)} />}
      {groups.map((group) => (
        <section key={group.title}>
          <h3 className="dl-label mb-2">{group.title}</h3>
          {group.title === "Figures" ? (
            <ul className={clsx("grid gap-2", group.files.length === 1 ? "grid-cols-1" : "grid-cols-2")}>
              {group.files.map((file) => (
                <li key={file.path}>
                  <button onClick={() => open(file)} title={file.path} className="group flex w-full flex-col text-left">
                    <span className="flex aspect-[4/3] w-full items-center justify-center rounded-[3px] border border-line bg-white p-1 group-hover:border-ink">
                      <img
                        src={api.fileUrl(conversationId, "outputs", file.path, file.checkpoint)}
                        alt=""
                        loading="lazy"
                        className="max-h-full max-w-full object-contain"
                      />
                    </span>
                    <span className="truncate pt-1.5 font-sans text-[12.5px] text-ink group-hover:underline">
                      {shown(file.path, prefix)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {group.files.map((file) => {
                const { dir } = splitPath(file.path);
                return (
                  <li key={file.path}>
                    <button
                      onClick={() => open(file)}
                      className="group flex w-full items-center gap-3 rounded-[3px] px-1.5 py-2 text-left hover:bg-surface"
                      title={file.path}
                    >
                      <FileGlyph kind={file.kind} size={26} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-sans text-[13.5px] font-medium group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">
                          {shown(file.path, prefix)}
                        </span>
                        <span className="block truncate font-sans text-[11.5px] text-muted">
                          {extension(file.path)} · {formatBytes(file.size)}
                          {dir && <span className="font-mono"> · {dir}</span>}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

/**
 * The start several file names share, cut at a separator, when it's long
 * enough to crowd out the part that tells them apart (sleep_mood_2025_pilot_).
 */
export function sharedPrefix(names: string[]): string {
  if (names.length < 2) return "";
  let prefix = names[0];
  for (const name of names.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < name.length && prefix[i] === name[i]) i++;
    prefix = prefix.slice(0, i);
  }
  const cut = Math.max(prefix.lastIndexOf("_"), prefix.lastIndexOf("-"), prefix.lastIndexOf(" "));
  prefix = cut >= 0 ? prefix.slice(0, cut + 1) : "";
  // Never swallow a whole name: every file keeps something to show.
  return prefix.length >= 8 && names.every((name) => name.length > prefix.length + 2) ? prefix : "";
}

function shown(path: string, prefix: string): string {
  const { name } = splitPath(path);
  return prefix && name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

function extension(path: string): string {
  const { name } = splitPath(path);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toUpperCase() : "File";
}

function History({ conversation }: { conversation: Conversation }) {
  const checkpoints = useQuery({
    queryKey: ["checkpoints", conversation.id],
    queryFn: () => api.checkpoints(conversation.id),
  });
  const [confirming, setConfirming] = useState<Checkpoint | null>(null);
  if (checkpoints.data?.length === 0) {
    return (
      <>
        <EmptyNote icon="history" title="No checkpoints yet">
          After each turn DataLab saves a copy of the workspace files, so you can always go back.
        </EmptyNote>
        <InfoTip term="checkpoint" className="mt-2" />
      </>
    );
  }
  return (
    <>
      <p className="mb-4 flex items-start gap-1 font-sans text-[13px] leading-relaxed text-muted">
        <span className="flex-1">A copy of the files after each turn. Restoring never loses the current ones.</span>
        <InfoTip term="checkpoint" align="end" />
      </p>
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
                <span className="font-sans text-[13.5px] font-medium">{checkpoint.label}</span>
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
              <div className="font-mono text-[11px] text-faint">
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

function DataAccessed({
  conversationId,
  onOpen,
  shown,
}: {
  conversationId: string;
  onOpen: (file: OpenFile) => void;
  shown?: { id: string } | null;
}) {
  const queries = useQuery({
    queryKey: ["data-accessed", conversationId],
    queryFn: () => api.dataAccessed(conversationId),
    refetchInterval: 3000,
  });
  const loaded = queries.data !== undefined;
  const [drafting, setDrafting] = useState(false);
  // The entry asked for: scrolled to, focused, and outlined for a few seconds.
  const [lit, setLit] = useState<string | null>(null);
  useEffect(() => {
    if (!shown || !loaded) return;
    const entry = document.getElementById(`query-${shown.id}`);
    entry?.scrollIntoView?.({ block: "center" });
    entry?.focus({ preventScroll: true });
    setLit(shown.id);
    const done = window.setTimeout(() => setLit(null), 4000);
    return () => window.clearTimeout(done);
  }, [shown, loaded]);
  if (queries.data?.length === 0) {
    return (
      <EmptyNote icon="db" title="No queries yet">
        Every read from the study database is listed here: which tables, how many rows, and the exact SQL.
      </EmptyNote>
    );
  }
  return (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <p className="dl-label flex min-w-[13rem] flex-1 items-center gap-1.5 !text-data">
          <Icon name="lock" size={11} /> Read-only · every query, newest last
          <InfoTip term="data-accessed" align="end" />
        </p>
        {queries.data?.some((q) => q.status === "succeeded") && (
          <Button variant="ghost" className="px-1.5 py-0.5 text-[12px]" onClick={() => setDrafting(true)}>
            <Icon name="history" size={12} /> Turn this into a workflow
          </Button>
        )}
      </div>
      {drafting && (
        <SaveAsWorkflow source={{ kind: "conversation", conversationId }} onClose={() => setDrafting(false)} />
      )}
      <ol className="flex flex-col gap-2">
        {queries.data?.map((q) => (
          <li
            key={q.id}
            id={`query-${q.id}`}
            tabIndex={-1}
            onBlur={() => setLit(null)}
            className={clsx("rounded-[4px] border bg-surface p-3 text-xs outline-none", q.id === lit ? "border-ink" : "border-line")}
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className={clsx(
                  "h-2 w-2 shrink-0 rounded-full",
                  q.status === "succeeded" ? "bg-data" : q.status === "running" ? "dl-breathe bg-attn" : "bg-danger",
                )}
              />
              <span className="min-w-0 flex-1 truncate font-sans text-[13.5px] font-medium">
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
              <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap bg-sunken p-2.5 font-mono text-[11.5px] leading-relaxed">
                {q.sql_text}
              </pre>
            </details>
            {q.result_file && q.status === "succeeded" && (
              <button
                className="mt-2 inline-flex items-center gap-1 font-medium text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
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
