import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/api/client";
import { Button, Modal } from "@/components/ui";
import { formatBytes, parseCsv } from "@/lib/csv";
import type { OpenFile } from "@/lib/files";

/**
 * Shows one workspace file. Agent-made HTML runs in a sandboxed frame with scripts off.
 *
 * The viewer shows one version of the file: the checkpoint it was listed
 * from, or the latest when it opened. Every view (page, source, table,
 * image) is of that version, and a newer one is only shown when asked for.
 */
export function FileViewer({ conversationId, file, onClose }: { conversationId: string; file: OpenFile; onClose: () => void }) {
  const [source, setSource] = useState(false);
  const checkpoints = useQuery({ queryKey: ["checkpoints", conversationId], queryFn: () => api.checkpoints(conversationId) });
  const latest = checkpoints.data?.[0]?.number;
  const [chosen, setChosen] = useState<number | null | undefined>(file.checkpoint);
  // Query results aren't in checkpoints; workspace files wait for a version.
  const live = file.root === "results";
  const version = live ? null : (chosen ?? latest);
  useEffect(() => {
    if (!live && chosen == null && latest != null) setChosen(latest);
  }, [live, chosen, latest]);
  const pinned: OpenFile = { ...file, checkpoint: version };
  const newer = !live && version != null && latest != null && latest > version;
  return (
    <Modal
      wide
      title={
        <span>
          {file.path}
          {file.size != null && <span className="ml-2 text-xs font-normal text-muted">{formatBytes(file.size)}</span>}
        </span>
      }
      onClose={onClose}
      actions={
        file.kind === "html" && (
          <Button variant="ghost" onClick={() => setSource(!source)}>
            {source ? "Show page" : "Show source"}
          </Button>
        )
      }
    >
      {!live && version != null && (
        <p className="mb-2 flex items-center gap-2 text-xs text-muted" data-testid="file-version">
          As saved at checkpoint {version}.
          {newer && (
            <>
              <span className="text-research">A newer version of the workspace has been saved.</span>
              <button type="button" className="text-accent underline" onClick={() => setChosen(latest)}>
                Show the newest
              </button>
            </>
          )}
        </p>
      )}
      {!live && version == null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : (
        <>
          {file.kind === "image" && (
            <img
              src={api.fileUrl(conversationId, file.root, file.path, version)}
              alt={file.path}
              className="mx-auto max-h-full max-w-full"
            />
          )}
          {file.kind === "html" && !source && <HtmlPreview conversationId={conversationId} file={pinned} />}
          {(file.kind === "text" || (file.kind === "html" && source)) && (
            <TextPreview conversationId={conversationId} file={pinned} />
          )}
          {file.kind === "csv" && <CsvPreview conversationId={conversationId} file={pinned} />}
        </>
      )}
      {(file.kind === "pdf" || file.kind === "other") && (
        <p className="text-sm text-muted">DataLab can't preview this kind of file yet.</p>
      )}
    </Modal>
  );
}

function HtmlPreview({ conversationId, file }: { conversationId: string; file: OpenFile }) {
  const preview = useQuery({
    // One preview link per version: a link always shows the checkpoint it was made for.
    queryKey: ["preview", conversationId, file.root, file.path, file.checkpoint],
    queryFn: () => api.preview(conversationId, file.root, file.path, file.checkpoint),
    staleTime: Infinity,
    gcTime: 0,
  });
  if (preview.error) return <p className="text-sm text-danger">{preview.error.message}</p>;
  if (!preview.data) return <p className="text-sm text-muted">Loading…</p>;
  return (
    <div className="flex h-full flex-col gap-2">
      <p className="text-xs text-muted">
        Shown with scripts turned off and no network access, so interactive parts of the page may not work.
      </p>
      {/* No sandbox allowances at all: no scripts, forms, or popups. DataLab also
          turns links in the page into plain text (backend htmlclean.py). */}
      <iframe
        title={file.path}
        src={preview.data.url}
        sandbox=""
        referrerPolicy="no-referrer"
        className="min-h-0 w-full flex-1 rounded-lg border border-line bg-white"
      />
    </div>
  );
}

function useText(conversationId: string, file: OpenFile) {
  return useQuery({
    queryKey: ["file-text", conversationId, file.root, file.path, file.checkpoint],
    queryFn: () => api.fileText(conversationId, file.root, file.path, file.checkpoint),
  });
}

function TextPreview({ conversationId, file }: { conversationId: string; file: OpenFile }) {
  const text = useText(conversationId, file);
  if (text.error) return <p className="text-sm text-danger">{text.error.message}</p>;
  if (!text.data) return <p className="text-sm text-muted">Loading…</p>;
  return (
    <>
      {text.data.truncated && <p className="mb-2 text-xs text-muted">Showing the start of the file.</p>}
      <pre className="whitespace-pre-wrap break-words font-mono text-xs">{text.data.text}</pre>
    </>
  );
}

const MAX_ROWS = 500;

function CsvPreview({ conversationId, file }: { conversationId: string; file: OpenFile }) {
  const text = useText(conversationId, file);
  if (text.error) return <p className="text-sm text-danger">{text.error.message}</p>;
  if (!text.data) return <p className="text-sm text-muted">Loading…</p>;
  const rows = parseCsv(text.data.text, {
    maxRows: MAX_ROWS + 1,
    delimiter: file.path.toLowerCase().endsWith(".tsv") ? "\t" : ",",
    truncated: text.data.truncated,
  });
  const [header, ...body] = rows;
  const more = text.data.truncated || body.length > MAX_ROWS;
  return (
    <>
      <p className="mb-2 text-xs text-muted">
        {more ? `Showing the first ${Math.min(body.length, MAX_ROWS).toLocaleString()} rows.` : `${body.length.toLocaleString()} rows.`}
      </p>
      <div className="overflow-auto rounded-lg border border-line">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 bg-sunken">
            <tr>
              {header?.map((cell, i) => (
                <th key={i} className="whitespace-nowrap px-2 py-1 text-left font-semibold">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.slice(0, MAX_ROWS).map((row, r) => (
              <tr key={r} className="border-t border-line">
                {row.map((cell, c) => (
                  <td key={c} className="whitespace-nowrap px-2 py-1 font-mono">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
