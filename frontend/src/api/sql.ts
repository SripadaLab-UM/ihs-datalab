// Calls for the SQL Playground. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type SqlStatus = Schemas["SqlStatus"];
export type SqlCheck = Schemas["CheckOut"];
export type SqlDiagnostic = Schemas["DiagnosticOut"];
export type SqlRun = Schemas["RunOut"];
export type SqlRunState = SqlRun["state"];
export type ResultPage = Schemas["ResultPageOut"];
export type ResultColumn = Schemas["ColumnOut"];
export type HistoryItem = Schemas["HistoryItem"];
export type CatalogCohort = Schemas["CatalogCohort"];
export type CatalogTable = Schemas["CatalogTable"];
export type CatalogHit = Schemas["CatalogHit"];
export type SqlProposal = Schemas["SqlProposalOut"];
export type ProposedBind = Schemas["ProposedBindOut"];
export type BindValue = string | number | null;

const post = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
  signal,
});
const id = encodeURIComponent;

export const sqlApi = {
  /** Whether the SQL Playground's backend is ready, and its limits. */
  status: () => request<SqlStatus>("/api/sql/status"),
  /** The SQL check's errors and warnings, with where they are. Nothing runs, and nothing is logged. */
  check: (sql: string, signal?: AbortSignal) => request<SqlCheck>("/api/sql/check", post({ sql }, signal)),
  /** Start a query. It runs in the background: follow it with `runStatus`. */
  run: (sql: string, binds: Record<string, BindValue>) => request<SqlRun>("/api/sql/runs", post({ sql, binds })),
  /** A run as it is now, or as soon as it finishes, waiting at most `wait` seconds. */
  runStatus: (runId: string, wait = 0, signal?: AbortSignal) =>
    request<SqlRun>(`/api/sql/runs/${id(runId)}?wait=${wait}`, { signal }),
  stop: (runId: string) => request<SqlRun>(`/api/sql/runs/${id(runId)}/stop`, { method: "POST" }),
  /** Rows of a result, within its preview limit. */
  results: (queryId: string, offset: number, limit: number) =>
    request<ResultPage>(`/api/sql/results/${id(queryId)}?offset=${offset}&limit=${limit}`),
  /** The Playground's own queries, newest first. */
  history: () => request<HistoryItem[]>("/api/sql/history"),
  /** Every cohort's tables and columns, with their comments. */
  catalog: () => request<CatalogCohort[]>("/api/sql/catalog"),
  search: (q: string) => request<CatalogHit[]>(`/api/sql/catalog/search?q=${id(q)}`),
  /** The queries a conversation's agent proposed for the editor (propose_sql): each turn's latest. Never run. */
  proposals: (conversationId: string) => request<SqlProposal[]>(`/api/sql/proposals/${id(conversationId)}`),
  /** Export a result's file to an export folder, with a manifest. */
  export: (queryId: string, destinationId: string) =>
    request<Schemas["ExportOut"]>(`/api/sql/results/${id(queryId)}/export`, post({ destination_id: destinationId })),
};
