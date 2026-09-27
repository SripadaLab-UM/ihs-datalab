import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { sqlApi } from "@/api/sql";
import { Button, Chip, EmptyNote, Icon } from "@/components/ui";

import { ExportResult } from "./ExportResult";

export const PAGE_ROWS = 50;

/** A finished result to show: from the run just now, or opened from history. */
export interface ShownResult {
  queryId: string;
  rowCount: number | null;
  /** How long the database took, if known. */
  seconds: number | null;
  tables: string[];
  warnings: string[];
}

/** The result grid: the first rows of a result, a page at a time, with its size and Export. */
export function ResultGrid({ result, practice }: { result: ShownResult; practice: boolean }) {
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const page = useQuery({
    queryKey: ["sql-results", result.queryId, offset],
    queryFn: () => sqlApi.results(result.queryId, offset, PAGE_ROWS),
    placeholderData: keepPreviousData,
    staleTime: Infinity, // a result never changes
  });
  const data = page.data?.query_id === result.queryId ? page.data : undefined;
  const limit = data?.preview_limit ?? 0;
  const count = result.rowCount ?? data?.row_count ?? null;
  const shownTo = data ? data.offset + data.rows.length : 0;
  const beyondPreview = count !== null && limit > 0 && count > limit;

  return (
    <section aria-label="Result" className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-5 py-2">
        <p className="font-sans text-[13.5px]">
          <span className="font-medium tabular">{count === null ? "?" : count.toLocaleString()}</span>{" "}
          {count === 1 ? "row" : "rows"}
          {result.seconds !== null && <span className="text-muted tabular"> · {formatSeconds(result.seconds)}</span>}
        </p>
        {result.tables.map((table) => (
          <Chip key={table}>{table}</Chip>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <Button variant="secondary" className="px-2.5 py-1 text-[12.5px]" onClick={() => setExporting(true)}>
            <Icon name="export" size={13} /> Export
          </Button>
        </div>
      </header>
      {result.warnings.map((warning) => (
        <p key={warning} className="border-b border-line bg-attn-soft px-5 py-1.5 font-sans text-[12.5px] text-attn">
          {warning}
        </p>
      ))}
      <div className="min-h-0 flex-1 overflow-auto">
        {page.isError && <p className="px-5 py-3 font-sans text-[13px] text-danger">{page.error.message}</p>}
        {data && data.columns.length > 0 && (
          <table className="min-w-full border-separate border-spacing-0 font-mono text-[12px]">
            <thead className="sticky top-0 z-[1] bg-surface">
              <tr>
                <th scope="col" className="border-b border-line px-3 py-1.5 text-right font-normal text-faint">
                  <span className="sr-only">Row</span>#
                </th>
                {data.columns.map((column, i) => (
                  <th key={i} scope="col" className="border-b border-line px-3 py-1.5 text-left align-bottom font-medium whitespace-nowrap">
                    <span className="block text-ink">{column.name}</span>
                    {column.type && <span className="block text-[10.5px] font-normal text-faint">{column.type}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, r) => (
                <tr key={data.offset + r} className="hover:bg-sunken">
                  <td className="border-b border-line/70 px-3 py-1 text-right text-faint tabular">{data.offset + r + 1}</td>
                  {row.map((cell, c) => (
                    <td
                      key={c}
                      title={cell.length > 40 ? cell : undefined}
                      className="max-w-[28rem] truncate border-b border-line/70 px-3 py-1 whitespace-nowrap text-ink"
                    >
                      {cell === "" ? <span className="text-faint">·</span> : cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && data.rows.length === 0 && data.offset === 0 && (
          <p className="px-5 py-4 font-serif text-[15px] text-muted italic">The query returned no rows.</p>
        )}
      </div>
      {data && data.rows.length > 0 && (
        <footer className="flex flex-wrap items-center gap-3 border-t border-line px-5 py-1.5 font-sans text-[12.5px] text-muted">
          <span className="tabular">
            Rows {(data.offset + 1).toLocaleString()}–{shownTo.toLocaleString()}
            {beyondPreview && ` of the first ${limit.toLocaleString()}`}
          </span>
          {beyondPreview && (
            <span>
              The grid shows the first {limit.toLocaleString()} rows. Export to get all {count!.toLocaleString()}.
            </span>
          )}
          <span className="ml-auto flex gap-1">
            <Button variant="ghost" className="px-2 py-0.5 text-[12.5px]" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_ROWS))}>
              Previous
            </Button>
            <Button variant="ghost" className="px-2 py-0.5 text-[12.5px]" disabled={!data.has_more} onClick={() => setOffset(offset + PAGE_ROWS)}>
              Next
            </Button>
          </span>
        </footer>
      )}
      {exporting && <ExportResult queryId={result.queryId} practice={practice} onClose={() => setExporting(false)} />}
    </section>
  );
}

/** What the results area says before there's a result, or when a run didn't give one. */
export function ResultNote({ tone, title, children }: { tone?: "bad" | "attn"; title: string; children?: React.ReactNode }) {
  return (
    <div className="px-5 py-5">
      <p className={tone === "bad" ? "font-sans text-[13.5px] font-medium text-danger" : "font-sans text-[13.5px] font-medium"}>
        {title}
      </p>
      {children && <div className="mt-1 max-w-[42rem] font-sans text-[13px] leading-relaxed text-muted">{children}</div>}
    </div>
  );
}

export function NothingYet({ previewRows }: { previewRows: number }) {
  return (
    <div className="px-5 py-5">
      <EmptyNote icon="table" title="Results appear here">
        Run your query with Run or {submitKeys()}. The grid shows the first {previewRows} rows; the whole result stays in
        DataLab until you export it.
      </EmptyNote>
    </div>
  );
}

export function formatSeconds(seconds: number): string {
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

/** The keys that run the query, as this computer writes them. */
export function submitKeys(): string {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  return mac ? "⌘↵" : "Ctrl+Enter";
}
