import { EditorView } from "@codemirror/view";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { sqlApi, type SqlCheck, type SqlRun } from "@/api/sql";

import { quoted } from "./CatalogBrowser";
import { SqlPage } from "./SqlPage";

vi.mock("@/api/sql", () => ({
  sqlApi: {
    status: vi.fn(),
    check: vi.fn(),
    run: vi.fn(),
    runStatus: vi.fn(),
    stop: vi.fn(),
    results: vi.fn(),
    history: vi.fn(),
    catalog: vi.fn(),
    search: vi.fn(),
    export: vi.fn(),
    proposals: vi.fn(async () => []),
  },
}));
vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "practice" })),
    catalogStatus: vi.fn(async () => ({ state: "empty", tables: 0, detail: "Sign in to GitHub and sync it." })),
    conversations: vi.fn(async () => []),
    destinations: vi.fn(async () => [{ id: "practice", name: "Practice exports", path: "/x", available: true }]),
  },
}));
// Save as workflow's dialog has its own tests (features/workflows): here, only what the page gives it.
vi.mock("@/api/workflows", () => ({
  workflowsApi: { destinations: vi.fn(async () => []), draft: vi.fn(() => new Promise(() => {})) },
}));
// The chat has its own tests: here, only what the page gives it.
const chatProps = vi.fn();
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: (props: unknown) => {
    chatProps(props);
    return <div>chat</div>;
  },
}));

// jsdom has no layout. CodeMirror measures text; give it empty boxes.
for (const proto of [Range.prototype, Element.prototype]) {
  proto.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as DOMRectList;
}
Range.prototype.getBoundingClientRect = () => new DOMRect();

const SQL = "SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA WHERE RECORD_DATE >= :d";

const passes: SqlCheck = { ok: true, errors: [], warnings: [], tables: ["IHS_2025.VFITBITDAILYDATA"], binds: ["d"] };

const run = (state: SqlRun["state"], extra: Partial<SqlRun> = {}): SqlRun => ({
  id: "pgr_1", state, sql: SQL, started_at: "", finished_at: null, message: null, diagnostic: null,
  query_id: null, row_count: null, bytes_written: null, elapsed_seconds: null, columns: [], tables: [], warnings: [],
  ...extra,
}); // prettier-ignore

const done = run("succeeded", {
  query_id: "q_20260927T120000_abcdef",
  row_count: 2,
  elapsed_seconds: 0.25,
  tables: ["IHS_2025.VFITBITDAILYDATA"],
  columns: [{ name: "STUDY_PARTICIPANT_ID", type: "VARCHAR2(64)" }],
});

beforeEach(() => {
  sessionStorage.clear();
  chatProps.mockReset();
  vi.mocked(sqlApi.status).mockResolvedValue({
    available: true, database_configured: true, playground_id: "pg_0", preview_rows: 200, max_rows: 1000, max_bytes: 1,
  }); // prettier-ignore
  vi.mocked(sqlApi.check).mockReset().mockResolvedValue(passes);
  vi.mocked(sqlApi.history).mockReset().mockResolvedValue([]);
  vi.mocked(sqlApi.catalog).mockResolvedValue([
    {
      schema_name: "IHS_2025",
      tables: [
        {
          name: "VFITBITDAILYDATA",
          type: "VIEW",
          comment: "Fitbit daily summary",
          primary_key: [],
          columns: [{ name: "TRACKERSTEPS", type: "NUMBER", nullable: true, comment: "Steps" }],
        },
      ],
    },
  ]);
  vi.mocked(sqlApi.run).mockReset().mockResolvedValue(run("running"));
  vi.mocked(sqlApi.runStatus).mockReset().mockResolvedValue(done);
  vi.mocked(sqlApi.stop).mockReset().mockResolvedValue(run("stopped", { message: "The query was stopped." }));
  vi.mocked(sqlApi.results).mockReset().mockResolvedValue({
    query_id: done.query_id!, columns: done.columns, row_count: 2, offset: 0,
    rows: [["SYN001"], ["SYN002"]], preview_limit: 200, has_more: false,
  }); // prettier-ignore
});

function show(sql = SQL) {
  sessionStorage.setItem("datalab:sql:draft", sql);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <SqlPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function editor() {
  const content = await screen.findByRole("textbox", { name: "SQL query" });
  return EditorView.findFromDOM(content)!;
}

it("checks the SQL as it's typed and says what the check found", async () => {
  vi.mocked(sqlApi.check).mockResolvedValue({
    ok: false,
    errors: [{ message: "Column 'NOPE' could not be resolved.", severity: "error", position: { line: 1, column: 8, end_line: 1, end_column: 12 } }],
    warnings: [], tables: [], binds: [],
  }); // prettier-ignore
  show("SELECT NOPE FROM IHS_2025.VFITBITDAILYDATA");
  expect(await screen.findByText(/Line 1, column 8: Column 'NOPE'/)).toBeInTheDocument();
  expect(sqlApi.check).toHaveBeenCalledWith("SELECT NOPE FROM IHS_2025.VFITBITDAILYDATA", expect.anything());
  expect(sqlApi.run).not.toHaveBeenCalled();
});

it("runs the query with its bind values and shows the result", async () => {
  show();
  expect(await screen.findByText(/Passes the SQL check/)).toBeInTheDocument();
  fireEvent.change(await screen.findByRole("textbox", { name: ":d" }), { target: { value: "2025-04-01" } });
  fireEvent.click(screen.getByRole("button", { name: /^Run/ }));
  await waitFor(() => expect(sqlApi.run).toHaveBeenCalledWith(SQL, { d: "2025-04-01" }));
  const result = await screen.findByRole("region", { name: "Result" });
  expect(await within(result).findByText("SYN002")).toBeInTheDocument();
  expect(within(result).getByText("VARCHAR2(64)")).toBeInTheDocument();
  expect(within(result).getByText("250 ms", { exact: false })).toBeInTheDocument();
  expect(sqlApi.results).toHaveBeenCalledWith(done.query_id, 0, 50);
});

it("runs with Ctrl+Enter in the editor", async () => {
  show("SELECT 1 FROM DUAL");
  const view = await editor();
  fireEvent.keyDown(view.contentDOM, { key: "Enter", code: "Enter", keyCode: 13, ctrlKey: true });
  await waitFor(() => expect(sqlApi.run).toHaveBeenCalledWith("SELECT 1 FROM DUAL", {}));
});

it("stops a running query", async () => {
  vi.mocked(sqlApi.runStatus).mockImplementation(() => new Promise(() => undefined));
  show("SELECT 1 FROM DUAL");
  fireEvent.click(await screen.findByRole("button", { name: /^Run/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Stop/ }));
  await waitFor(() => expect(sqlApi.stop).toHaveBeenCalledWith("pgr_1"));
  expect(await screen.findByText("Stopped.")).toBeInTheDocument();
});

it("says why a refused query wasn't run", async () => {
  vi.mocked(sqlApi.run).mockResolvedValue(
    run("rejected", {
      message: "Only SELECT queries (optionally with WITH) are allowed.",
      diagnostic: { message: "", severity: "error", position: { line: 1, column: 1, end_line: 1, end_column: 7 } },
    }),
  );
  show("DELETE FROM IHS_2025.VFITBITDAILYDATA");
  fireEvent.click(await screen.findByRole("button", { name: /^Run/ }));
  expect(await screen.findByText(/refused this query, so it wasn't run/)).toBeInTheDocument();
  expect(screen.getByText(/Line 1, column 1: Only SELECT/)).toBeInTheDocument();
});

it("puts a table's or a column's name in the query from the catalog", async () => {
  show("SELECT ");
  const view = await editor();
  view.dispatch({ selection: { anchor: view.state.doc.length } });
  fireEvent.click(await screen.findByRole("button", { name: /Show the columns of IHS_2025.VFITBITDAILYDATA/ }));
  fireEvent.click(screen.getByTitle(/Put TRACKERSTEPS in your query/));
  fireEvent.click(screen.getByTitle("Put IHS_2025.VFITBITDAILYDATA in your query"));
  await waitFor(() => expect(view.state.doc.toString()).toBe("SELECT TRACKERSTEPS IHS_2025.VFITBITDAILYDATA"));
});

it("with no catalog, the table list says why and how it gets one", async () => {
  vi.mocked(sqlApi.catalog).mockResolvedValue([]);
  show("");
  expect(await screen.findByText(/no query can be checked or run\. Sign in to GitHub and sync it\./)).toBeInTheDocument();
});

it("quotes column names Oracle can't read bare", () => {
  expect(quoted("TRACKERSTEPS")).toBe("TRACKERSTEPS");
  expect(quoted("steps")).toBe('"steps"');
});

it("opens a query from history, with its SQL and result", async () => {
  vi.mocked(sqlApi.history).mockResolvedValue([
    {
      query_id: done.query_id!, status: "succeeded", sql: "SELECT 2 FROM DUAL", binds: {}, tables: [],
      started_at: new Date().toISOString(), finished_at: null, row_count: 2, elapsed_ms: 40, message: null, has_result: true,
    },
  ]); // prettier-ignore
  show("SELECT 1 FROM DUAL");
  fireEvent.click(await screen.findByRole("tab", { name: "History" }));
  fireEvent.click(await screen.findByText("SELECT 2 FROM DUAL"));
  const view = await editor();
  await waitFor(() => expect(view.state.doc.toString()).toBe("SELECT 2 FROM DUAL"));
  expect(await screen.findByText("SYN001")).toBeInTheDocument();
});

it("docks a SQL drafting chat that is offered the SQL, not sent it", async () => {
  sessionStorage.setItem("datalab:sql:chat-open", "open");
  show("SELECT 1 FROM DUAL");
  await waitFor(() => expect(chatProps).toHaveBeenCalled());
  const props = chatProps.mock.lastCall![0] as { mode: string; context: { text: string; language: string } };
  expect(props.mode).toBe("sql");
  expect(props.context).toMatchObject({ text: "SELECT 1 FROM DUAL", language: "sql" });
});

it("exports a result to the chosen folder", async () => {
  vi.mocked(sqlApi.export).mockResolvedValue({
    folder: "/x/2026-09-27 SQL Playground",
    files: [],
    destination_id: "practice",
    destination_name: "Practice exports",
    saved_to: "Saved to Practice exports (on this computer)",
    sync_provider: null,
    sync_note: null,
  });
  show("SELECT 1 FROM DUAL");
  fireEvent.click(await screen.findByRole("button", { name: /^Run/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Export/ }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByRole("option", { name: /Practice exports/ });
  fireEvent.click(within(dialog).getByRole("button", { name: "Export" }));
  await waitFor(() => expect(sqlApi.export).toHaveBeenCalledWith(done.query_id, "practice"));
  expect(await within(dialog).findByText("/x/2026-09-27 SQL Playground")).toBeInTheDocument();
});

it("reads the rows with the arrow keys, one tab stop for the grid", async () => {
  const long = "x".repeat(120);
  vi.mocked(sqlApi.results).mockResolvedValue({
    query_id: done.query_id!, columns: done.columns, row_count: 2, offset: 0,
    rows: [["SYN001"], [long]], preview_limit: 200, has_more: false,
  }); // prettier-ignore
  show("SELECT 1 FROM DUAL");
  fireEvent.click(await screen.findByRole("button", { name: /^Run/ }));
  const rows = await screen.findByRole("region", { name: "Result rows" });
  expect(rows).toHaveAttribute("tabindex", "0");
  const cell = await within(rows).findByText(long);
  expect(within(rows).queryAllByRole("cell").some((c) => c.hasAttribute("tabindex"))).toBe(false);
  expect(cell.className).toContain("truncate");
  fireEvent.keyDown(rows, { key: "ArrowDown" });
  fireEvent.keyDown(rows, { key: "ArrowDown" });
  // The row it's on shows its values in full, and is read out.
  expect(cell.className).not.toContain("truncate");
  expect(cell.closest("tr")).toHaveAttribute("aria-current", "true");
  expect(within(rows).getByText(/^Row 2: STUDY_PARTICIPANT_ID x+$/)).toBeInTheDocument();
});

it("offers Save as workflow for a query that passes the check, with its binds' values", async () => {
  const { workflowsApi } = await import("@/api/workflows");
  show();
  const button = await screen.findByRole("button", { name: /Save as workflow/ });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.change(await screen.findByRole("textbox", { name: ":d" }), { target: { value: "2025-04-01" } });
  fireEvent.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Save as workflow" });
  expect(within(dialog).getByRole("textbox", { name: /Name/ })).toHaveValue("vfitbitdailydata");
  fireEvent.click(within(dialog).getByRole("button", { name: "Draft the workflow" }));
  await waitFor(() => expect(workflowsApi.draft).toHaveBeenCalled());
  expect(vi.mocked(workflowsApi.draft).mock.calls[0][0].queries).toEqual([{ sql: SQL, binds: { d: "2025-04-01" } }]);
});

it("doesn't offer Save as workflow for a query the check refuses", async () => {
  vi.mocked(sqlApi.check).mockResolvedValue({
    ok: false, errors: [{ message: "Only SELECT queries are allowed.", severity: "error", position: null }],
    warnings: [], tables: [], binds: [],
  }); // prettier-ignore
  show("DELETE FROM IHS_2025.VFITBITDAILYDATA");
  expect(await screen.findByText(/Only SELECT queries are allowed/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Save as workflow/ })).toBeDisabled();
});
