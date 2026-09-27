import { useQuery } from "@tanstack/react-query";

import { type HistoryItem, sqlApi } from "@/api/sql";
import { Chip, EmptyNote } from "@/components/ui";

import { formatSeconds } from "./Results";

const STATUS: Record<string, { label: string; tone?: "good" | "attn" | "bad" }> = {
  succeeded: { label: "ran", tone: "good" },
  running: { label: "running" },
  rejected: { label: "refused", tone: "bad" },
  failed: { label: "failed", tone: "bad" },
  cancelled: { label: "stopped", tone: "attn" },
};

/** The Playground's own queries, newest first. Opening one puts its SQL back in the
 *  editor and shows its result, if it has one. */
export function History({ onOpen }: { onOpen: (item: HistoryItem) => void }) {
  const history = useQuery({ queryKey: ["sql-history"], queryFn: sqlApi.history });
  if (history.isError) return <p className="px-4 py-3 font-sans text-[13px] text-danger">The history couldn't be loaded.</p>;
  if (history.isSuccess && history.data.length === 0) {
    return (
      <div className="px-4 py-3">
        <EmptyNote icon="history" title="No queries yet">
          Each query you run is listed here, with how it went. Open one to see its SQL and result again.
        </EmptyNote>
      </div>
    );
  }
  return (
    <ol className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
      {history.data?.map((item) => {
        const status = STATUS[item.status] ?? { label: item.status };
        return (
          <li key={item.query_id}>
            <button
              type="button"
              onClick={() => onOpen(item)}
              className="group flex w-full flex-col gap-1 rounded-[3px] px-2 py-2 text-left hover:bg-sunken"
            >
              <span className="flex items-center gap-2 font-sans text-[11.5px] text-muted">
                <Chip tone={status.tone}>{status.label}</Chip>
                <time dateTime={item.started_at} className="tabular">
                  {when(item.started_at)}
                </time>
                {item.row_count !== null && (
                  <span className="tabular">
                    {item.row_count.toLocaleString()} {item.row_count === 1 ? "row" : "rows"}
                  </span>
                )}
                {item.elapsed_ms !== null && <span className="tabular">{formatSeconds(item.elapsed_ms / 1000)}</span>}
              </span>
              <code className="line-clamp-3 font-mono text-[11.5px] leading-snug whitespace-pre-wrap [overflow-wrap:anywhere] text-ink group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">
                {item.sql}
              </code>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function when(iso: string): string {
  const date = new Date(iso);
  const today = new Date().toDateString() === date.toDateString();
  return today
    ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
