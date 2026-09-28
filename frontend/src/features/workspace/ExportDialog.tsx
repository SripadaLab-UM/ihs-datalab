import clsx from "clsx";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api, type Conversation, type FileRoot, type WorkspaceFile } from "@/api/client";
import { Button, FileGlyph, Icon, Modal } from "@/components/ui";
import { kindOf } from "@/lib/files";
import { formatBytes } from "@/lib/csv";
import { settingsLink } from "@/features/settings/highlight";

import { buildReport, REPORT_CSS } from "./report";

/**
 * Export files and, optionally, the conversation as a report. This is the
 * only way anything leaves DataLab, and only the person can do it.
 */
export function ExportDialog({
  conversation,
  withReport,
  onClose,
}: {
  conversation: Conversation;
  withReport: boolean;
  onClose: () => void;
}) {
  const outputs = useQuery({ queryKey: ["files", conversation.id], queryFn: () => api.files(conversation.id) });
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: api.destinations });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  // The listing the person chooses from, frozen when it first loads: the files
  // exported are exactly the versions shown, even if the agent saves new ones.
  const [shown, setShown] = useState<{ checkpoint: number | null; files: WorkspaceFile[] } | null>(null);
  const listed = outputs.data ? { checkpoint: outputs.data[0]?.checkpoint ?? null, files: outputs.data } : null;
  useEffect(() => {
    if (!shown && listed) setShown(listed);
  }, [shown, listed]);
  const newer = shown && listed && listed.checkpoint !== shown.checkpoint;
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [rawHtml, setRawHtml] = useState(false);
  const [report, setReport] = useState(withReport);
  const [includeWork, setIncludeWork] = useState(false);
  const [destination, setDestination] = useState<string>("");
  // Nothing is chosen for the person: they pick what leaves.
  const chosen = picked;
  const pages = [...chosen].filter((p) => /\.(html?|svg)$/i.test(p));
  const destinationId = destination || destinations.data?.[0]?.id || "";

  const run = useMutation({
    mutationFn: async () => {
      let reportBody: { html: string; css: string } | undefined;
      if (report) {
        const [events, queries] = await Promise.all([
          api.allEvents(conversation.id),
          conversation.kind === "data" ? api.dataAccessed(conversation.id) : Promise.resolve([]),
        ]);
        reportBody = { html: await buildReport(conversation, events, queries, { includeWork, practice }), css: REPORT_CSS };
      }
      const files = [...chosen].map((path) => ({ root: "outputs" as FileRoot, path }));
      // The versions listed: never "whatever is latest" at the moment of export.
      return api.export(conversation.id, destinationId, files, reportBody, shown?.checkpoint ?? undefined, rawHtml);
    },
  });

  const toggle = (path: string) => {
    const next = new Set(chosen);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setPicked(next);
  };

  return (
    <Modal title="Export" onClose={onClose}>
      {run.data ? (
        <div className="flex flex-col gap-3 text-sm">
          <p role="status">
            {run.data.saved_to}: {run.data.files.length} file{run.data.files.length === 1 ? "" : "s"}, in
          </p>
          <p className="break-all rounded-lg bg-sunken px-3 py-2 font-mono text-xs">{run.data.folder}</p>
          {run.data.sync_note && <p className="text-muted">{run.data.sync_note}</p>}
          {conversation.kind === "data" && !practice && (
            <p className="text-research">These files may contain study data. Keep them on approved storage.</p>
          )}
          <div className="flex justify-end">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4 text-sm">
          <label
            className={clsx(
              "flex cursor-pointer items-start gap-3 rounded-xl border p-3",
              report ? "border-accent bg-accent-soft" : "border-line hover:bg-sunken",
            )}
          >
            <input type="checkbox" checked={report} onChange={(e) => setReport(e.target.checked)} className="mt-1" />
            <FileGlyph kind="html" />
            <span className="flex-1">
              The conversation, as one self-contained web page
              <span className="block text-xs text-muted">Questions, answers, charts, and the SQL that was run.</span>
            </span>
          </label>
          {report && (
            <label className="-mt-2 ml-7 flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={includeWork} onChange={(e) => setIncludeWork(e.target.checked)} />
              Include the agent's reasoning and the commands it ran (never their output)
            </label>
          )}
          <div>
            <div className="flex items-center justify-between">
              <p className="text-[11.5px] font-semibold uppercase tracking-[0.08em] text-faint">Files from outputs/</p>
              {(shown?.files.length ?? 0) > 0 && (
                <button
                  type="button"
                  className="text-xs text-accent underline decoration-faint underline-offset-4 hover:decoration-ink"
                  onClick={() => setPicked(chosen.size ? new Set() : new Set(shown?.files.map((f) => f.path)))}
                >
                  {chosen.size ? "Select none" : "Select all"}
                </button>
              )}
            </div>
            {shown?.checkpoint != null && (
              <p className="text-xs text-muted" data-testid="export-version">
                As saved at checkpoint {shown.checkpoint}.
              </p>
            )}
            {newer && (
              <p className="mt-1 rounded-lg bg-research-soft px-2 py-1 text-xs text-research" role="status">
                The agent has saved newer files since you opened this.{" "}
                <button
                  type="button"
                  className="underline hover:decoration-2"
                  onClick={() => {
                    // A different version is a different choice: pick again.
                    setShown(listed);
                    setPicked(new Set());
                  }}
                >
                  Show the newer files
                </button>{" "}
                (you'll choose again).
              </p>
            )}
            {shown?.files.length === 0 && <p className="text-xs text-muted">No output files yet.</p>}
            <ul className="mt-1.5 flex max-h-56 flex-col gap-0.5 overflow-y-auto">
              {shown?.files.map((file) => (
                <li key={file.path}>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded-lg px-1.5 py-1 hover:bg-sunken">
                    <input type="checkbox" checked={chosen.has(file.path)} onChange={() => toggle(file.path)} />
                    <FileGlyph kind={kindOf(file.path)} size={24} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">{file.path}</span>
                    <span className="text-xs text-muted">{formatBytes(file.size)}</span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
          {pages.length > 0 && (
            <div className="rounded-lg bg-sunken p-2 text-xs">
              <p>
                Web pages and SVG images made by the agent are exported as inert copies: nothing in them can contact
                the internet when opened, and interactive parts won't work.
              </p>
              <label className="mt-1 flex items-center gap-2 text-research">
                <input type="checkbox" checked={rawHtml} onChange={(e) => setRawHtml(e.target.checked)} />
                Keep web pages exactly as the agent made them (their scripts could send data when opened)
              </label>
            </div>
          )}
          <label className="block">
            <span className="text-[11.5px] font-semibold uppercase tracking-[0.08em] text-faint">To</span>
            {destinations.data?.length === 0 ? (
              <p className="mt-1 text-xs text-muted">
                No export folders yet. Add one in <Link {...settingsLink("export-folders", "export-folders")} className="text-accent underline decoration-faint underline-offset-4 hover:decoration-ink">Settings</Link>.
              </p>
            ) : (
              <select
                value={destinationId}
                onChange={(e) => setDestination(e.target.value)}
                className="mt-1.5 w-full rounded-xl border border-line bg-canvas px-3 py-2 outline-none focus:border-accent"
              >
                {destinations.data?.map((d) => (
                  <option key={d.id} value={d.id} disabled={!d.available}>
                    {d.name} — {d.path}
                  </option>
                ))}
              </select>
            )}
          </label>
          {conversation.kind === "data" && !practice && (
            <p className="text-xs text-research">This is a data session: what you export may contain study data.</p>
          )}
          {run.error && <p className="text-danger">{run.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              disabled={
                run.isPending || !destinationId || (!report && chosen.size === 0) || (chosen.size > 0 && shown?.checkpoint == null)
              }
              onClick={() => run.mutate()}
            >
              <Icon name="export" size={14} /> {run.isPending ? "Exporting…" : "Export"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
