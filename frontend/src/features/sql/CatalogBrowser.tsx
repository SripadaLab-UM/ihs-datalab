import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";

import { type CatalogTable, sqlApi } from "@/api/sql";
import { EmptyNote, Icon } from "@/components/ui";
import { LoadFailed, LoadingRows } from "@/components/ui/Loading";

/**
 * Every cohort's tables and columns, with their descriptions. Clicking a
 * table's name puts `SCHEMA.TABLE` at the cursor in the editor; clicking a
 * column puts its name there. Metadata only: no values are shown.
 */
export function CatalogBrowser({ onInsert }: { onInsert: (text: string) => void }) {
  const catalog = useQuery({ queryKey: ["sql-catalog"], queryFn: sqlApi.catalog, staleTime: 10 * 60_000 });
  const [typed, setTyped] = useState("");
  const words = useDebounced(typed.trim(), 200);
  const hits = useQuery({
    queryKey: ["sql-catalog-search", words],
    queryFn: () => sqlApi.search(words),
    enabled: words.length > 1,
    staleTime: 60_000,
  });
  // Newest cohort first. Every cohort starts folded: the person opens the one they want.
  const cohorts = useMemo(
    () => [...(catalog.data ?? [])].sort((a, b) => b.schema_name.localeCompare(a.schema_name)),
    [catalog.data],
  );
  const tables = useMemo(() => {
    const found = new Map<string, CatalogTable>();
    for (const cohort of cohorts) for (const t of cohort.tables) found.set(`${cohort.schema_name}.${t.name}`, t);
    return found;
  }, [cohorts]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-4 pt-3 pb-2">
        <label className="relative block">
          <span className="sr-only">Search tables and columns</span>
          <Icon name="search" size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
          <input
            type="search"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="Search tables and columns"
            className="w-full rounded-[3px] border border-line bg-field py-1.5 pr-2 pl-8 font-sans text-[13px] outline-none placeholder:text-faint focus:border-ink"
          />
        </label>
        <p className="mt-1.5 font-sans text-[11.5px] text-faint">Click a name to put it in your query.</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {catalog.isError && (
          <LoadFailed message="The catalog couldn't be loaded." onRetry={() => void catalog.refetch()} retrying={catalog.isFetching} />
        )}
        {catalog.isPending && words.length <= 1 && <LoadingRows label="Loading the tables…" rows={5} dense />}
        {catalog.isSuccess && cohorts.length === 0 && (
          <EmptyNote icon="table" title="No tables yet">
            DataLab has no catalog of the cohorts' tables yet. Settings → About this DataLab says why, and what
            happens next.
          </EmptyNote>
        )}
        {words.length > 1 ? (
          <section aria-label="Search results">
            <p className="dl-label mb-1.5">{hits.isSuccess ? `${hits.data.length} matching tables` : "Searching…"}</p>
            <ul className="flex flex-col">
              {hits.data?.map((hit) => {
                const name = `${hit.schema_name}.${hit.name}`;
                const table = tables.get(name);
                return table ? (
                  <TableRow key={name} schema={hit.schema_name} table={table} highlight={hit.matching_columns} onInsert={onInsert} showSchema />
                ) : null;
              })}
            </ul>
          </section>
        ) : (
          cohorts.map((cohort) => (
            <details key={cohort.schema_name} className="group/cohort border-t border-line first:border-t-0">
              <summary className="flex list-none items-center gap-1.5 py-2 font-mono text-[12.5px] font-medium text-ink select-none hover:underline hover:decoration-faint hover:underline-offset-4 [&::-webkit-details-marker]:hidden">
                <Icon name="chevron" size={12} className="text-faint transition-transform group-open/cohort:rotate-90" />
                {cohort.schema_name}
                <span className="font-sans text-[11.5px] font-normal text-faint">{cohort.tables.length} tables</span>
              </summary>
              <ul className="flex flex-col pb-2">
                {cohort.tables.map((table) => (
                  <TableRow key={table.name} schema={cohort.schema_name} table={table} onInsert={onInsert} />
                ))}
              </ul>
            </details>
          ))
        )}
      </div>
    </div>
  );
}

function TableRow({
  schema,
  table,
  onInsert,
  highlight = [],
  showSchema = false,
}: {
  schema: string;
  table: CatalogTable;
  onInsert: (text: string) => void;
  highlight?: string[];
  showSchema?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const qualified = `${schema}.${table.name}`;
  return (
    <li>
      <div className="group flex items-start gap-1">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} the columns of ${qualified}`}
          className="mt-[3px] shrink-0 p-0.5 text-faint hover:text-ink"
        >
          <Icon name="chevron" size={11} className={clsx("transition-transform", open && "rotate-90")} />
        </button>
        <button
          type="button"
          onClick={() => onInsert(qualified)}
          title={`Put ${qualified} in your query`}
          className="min-w-0 flex-1 py-0.5 text-left"
        >
          <span className="block truncate font-mono text-[12px] text-ink group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">
            {showSchema && <span className="text-faint">{schema}.</span>}
            {table.name}
          </span>
          {table.comment && <span className="block truncate font-sans text-[11.5px] text-muted">{table.comment}</span>}
        </button>
      </div>
      {!open && highlight.length > 0 && (
        <p className="mb-1 ml-[18px] flex flex-wrap gap-x-2 pl-2 font-sans text-[11.5px] text-faint">
          Columns:
          {highlight.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => onInsert(quoted(name))}
              title={`Put ${name} in your query`}
              className="font-mono text-ink hover:underline"
            >
              {name}
            </button>
          ))}
        </p>
      )}
      {open && (
        <ul className="mb-1.5 ml-[18px] border-l border-line pl-2">
          {table.columns.map((column) => (
            <li key={column.name}>
              <button
                type="button"
                onClick={() => onInsert(quoted(column.name))}
                title={[`Put ${column.name} in your query`, column.comment].filter(Boolean).join("\n")}
                className={clsx(
                  "flex w-full items-baseline gap-2 py-[1px] text-left hover:bg-sunken",
                  highlight.includes(column.name) && "bg-accent-soft",
                )}
              >
                <span className="min-w-0 truncate font-mono text-[11.5px] text-ink">{column.name}</span>
                <span className="ml-auto shrink-0 font-mono text-[11.5px] text-faint">{column.type}</span>
              </button>
              {column.comment && <p className="-mt-0.5 mb-0.5 truncate font-sans text-[11.5px] text-muted">{column.comment}</p>}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** A column name as Oracle needs it written: quoted unless it's a plain upper-case name. */
export function quoted(name: string): string {
  return /^[A-Z][A-Z0-9_$#]*$/.test(name) ? name : `"${name.replace(/"/g, '""')}"`;
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
