import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";

import { api } from "@/api/client";
import { Button, Chip, FileGlyph, Icon, Modal } from "@/components/ui";
import { formatBytes, parseCsv } from "@/lib/csv";
import { provenanceApi } from "@/api/provenance";
import { showQuery } from "@/components/chat/provenance";
import { kindOf, type OpenFile } from "@/lib/files";

import { HowWasThisMade } from "./HowWasThisMade";

/**
 * Shows one workspace file. Agent-made HTML runs in a sandboxed frame with scripts off.
 *
 * The viewer shows one version of the file: the checkpoint it was listed
 * from, or the latest when it opened. Every view (page, source, table,
 * image) is of that version, and a newer one is only shown when asked for.
 */
export function FileViewer({
  conversationId,
  file,
  onOpen,
  onClose,
}: {
  conversationId: string;
  file: OpenFile;
  /** Open another file (a script, from How was this made?). */
  onOpen?: (file: OpenFile) => void;
  onClose: () => void;
}) {
  const [source, setSource] = useState(false);
  const [howMade, setHowMade] = useState(false);
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
        <span className="flex min-w-0 items-center gap-2.5">
          <FileGlyph kind={file.kind} size={28} />
          <span className="min-w-0 truncate">{file.path}</span>
          {file.size != null && <span className="shrink-0 text-xs font-normal text-muted">{formatBytes(file.size)}</span>}
        </span>
      }
      onClose={onClose}
      actions={
        <>
          {!live && (
            <Button variant="ghost" onClick={() => setHowMade(!howMade)} aria-expanded={howMade}>
              <Icon name="history" size={14} /> How was this made?
            </Button>
          )}
          {file.kind === "html" && (
            <Button variant="ghost" onClick={() => setSource(!source)}>
              <Icon name={source ? "eye" : "code"} size={14} /> {source ? "Show page" : "Show source"}
            </Button>
          )}
        </>
      }
    >
      {!live && version != null && (
        <p className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted" data-testid="file-version">
          <Icon name="history" size={13} /> As saved at checkpoint {version}.
          {newer && (
            <span className="inline-flex items-center gap-2 rounded-full bg-attn-soft px-2.5 py-0.5 text-attn">
              A newer version has been saved.
              <button type="button" className="font-medium underline hover:decoration-2" onClick={() => setChosen(latest)}>
                Show the newest
              </button>
            </span>
          )}
        </p>
      )}
      {howMade && !live && (
        <div className="mb-4 border border-line bg-surface px-4 py-3">
          <FileProvenancePanel conversationId={conversationId} file={file} version={version} onOpen={onOpen} onClose={onClose} />
        </div>
      )}
      {!live && version == null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : (
        <>
          {file.kind === "image" && (
            <div className="flex h-full items-center justify-center rounded-xl bg-sunken p-4">
              <img
                src={api.fileUrl(conversationId, file.root, file.path, version)}
                alt={file.path}
                className="max-h-full max-w-full rounded-md bg-white shadow-sm"
              />
            </div>
          )}
          {file.kind === "html" && !source && <HtmlPreview conversationId={conversationId} file={pinned} />}
          {(file.kind === "text" || (file.kind === "html" && source)) && (
            <TextPreview conversationId={conversationId} file={pinned} />
          )}
          {file.kind === "csv" && <CsvPreview conversationId={conversationId} file={pinned} />}
        </>
      )}
      {(file.kind === "pdf" || file.kind === "other") && (
        <p className="rounded-xl bg-sunken p-6 text-center text-sm text-muted">
          DataLab can't preview this kind of file yet. You can export it and open it on your computer.
        </p>
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
      <p className="flex items-center gap-1.5 text-xs text-muted">
        <Icon name="shield" size={13} /> Shown with scripts turned off and no network access, so interactive parts of the page may not work.
      </p>
      {/* No sandbox allowances at all: no scripts, forms, or popups. DataLab also
          turns links in the page into plain text (backend htmlclean.py). */}
      <iframe
        title={file.path}
        src={preview.data.url}
        sandbox=""
        referrerPolicy="no-referrer"
        // Kept out of the Tab order: focus inside a frame can't be held in the
        // dialog, and with scripts off the page has nothing to operate.
        tabIndex={-1}
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
      <pre className="whitespace-pre-wrap break-words rounded-xl bg-sunken p-4 font-mono text-xs leading-relaxed">
        {text.data.text}
      </pre>
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
  const shown = body.slice(0, MAX_ROWS);
  const more = text.data.truncated || body.length > MAX_ROWS;
  // Right-align a column when every value in it is a number, like a spreadsheet does.
  const numeric = (header ?? []).map((_, c) => {
    const values = shown.map((row) => row[c] ?? "").filter((v) => v !== "");
    return values.some((v) => /\d/.test(v)) && values.every((v) => NUMBER.test(v));
  });
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <Chip tone="good">
          {more ? `first ${shown.length.toLocaleString()} rows` : `${shown.length.toLocaleString()} row${shown.length === 1 ? "" : "s"}`}
        </Chip>
        <Chip>
          {(header?.length ?? 0).toLocaleString()} column{header?.length === 1 ? "" : "s"}
        </Chip>
      </div>
      <div className="max-h-[calc(85vh-10rem)] overflow-auto rounded-xl border border-line">
        <table className="min-w-full border-separate border-spacing-0 text-xs tabular-nums">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-[2] border-b border-line bg-sunken px-2 py-1.5 text-right font-normal text-faint">
                #
              </th>
              {header?.map((cell, i) => (
                <th
                  key={i}
                  className={clsx(
                    "sticky top-0 z-[1] whitespace-nowrap border-b border-line bg-sunken px-3 py-1.5 font-semibold",
                    numeric[i] ? "text-right" : "text-left",
                  )}
                >
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, r) => (
              <tr key={r} className="odd:bg-surface even:bg-sunken hover:bg-accent-soft">
                <td className="sticky left-0 bg-inherit px-2 py-1 text-right text-faint">{r + 1}</td>
                {row.map((cell, c) => (
                  <td key={c} className={clsx("whitespace-nowrap px-3 py-1 font-mono", numeric[c] && "text-right")}>
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

const NUMBER = /^[-+]?(\d[\d,]*(\.\d*)?|\.\d+)([eE][-+]?\d+)?$|^NA$|^NaN$/;

/** "How was this made?" for a workspace file, as the version shown was saved. */
function FileProvenancePanel({
  conversationId,
  file,
  version,
  onOpen,
  onClose,
}: {
  conversationId: string;
  file: OpenFile;
  version: number | null | undefined;
  onOpen?: (file: OpenFile) => void;
  onClose: () => void;
}) {
  const inWork = file.root === "outputs" ? `outputs/${file.path}` : file.path;
  // The version being shown, so the chain is about the same file.
  const made = useQuery({
    queryKey: ["provenance", conversationId, inWork, version],
    queryFn: () => provenanceApi.file(conversationId, inWork, version),
    enabled: version != null,
  });
  if (made.isError) return <p className="text-sm text-danger">DataLab couldn't work out how this file was made.</p>;
  if (!made.data) return <p className="text-sm text-muted">Looking back through the checkpoints…</p>;
  return (
    <HowWasThisMade
      provenance={made.data}
      openQuery={(id) => {
        onClose();
        showQuery(id);
      }}
      openScript={onOpen ? (path, checkpoint) => onOpen({ root: "work", path, kind: kindOf(path), checkpoint }) : undefined}
    />
  );
}
