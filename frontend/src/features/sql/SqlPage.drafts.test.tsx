// Describe → generate → review → run: the agent's proposed query and the editor's draft.
import { EditorView } from "@codemirror/view";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { type SqlCheck, type SqlProposal, type SqlRun, sqlApi } from "@/api/sql";

import { nextProposal, onArrival } from "./drafts";
import { SqlPage } from "./SqlPage";

vi.mock("@/api/sql", () => ({
  sqlApi: {
    status: vi.fn(),
    check: vi.fn(),
    run: vi.fn(),
    runStatus: vi.fn(),
    stop: vi.fn(),
    results: vi.fn(),
    history: vi.fn(async () => []),
    catalog: vi.fn(),
    search: vi.fn(),
    export: vi.fn(),
    proposals: vi.fn(),
  },
}));
vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "practice" })),
    conversations: vi.fn(),
    destinations: vi.fn(async () => []),
  },
}));
vi.mock("@/api/workflows", () => ({ workflowsApi: { destinations: vi.fn(async () => []), draft: vi.fn() } }));
const chatProps = vi.fn();
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: (props: { onSending?: () => void; headerActions?: React.ReactNode }) => {
    chatProps(props);
    return (
      <div>
        {props.headerActions}
        <textarea aria-label="Your question or instruction" />
        <div data-question-seq="1">the turn</div>
      </div>
    );
  },
}));

// The editor loads its language on demand: give it time on a busy machine (the whole suite runs at once).
configure({ asyncUtilTimeout: 5000 });

for (const proto of [Range.prototype, Element.prototype]) {
  proto.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as DOMRectList;
}
Range.prototype.getBoundingClientRect = () => new DOMRect();

const MINE = "SELECT 1 FROM DUAL";
const STEPS =
  "SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA\n" +
  "WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD') AND RECORD_DATE < TO_DATE(:end_date, 'YYYY-MM-DD')";

const proposal = (extra: Partial<SqlProposal> = {}): SqlProposal => ({
  proposal_id: "sp_1", conversation_id: "c1", turn: 1, seq: 9, created_at: "2026-09-28T10:00:00.000+00:00",
  turn_status: "completed", turn_done: true, request: "Daily Fitbit steps for enrolled 2025 participants in March",
  request_seq: 1, title: "Daily Fitbit steps, March 2025", sql: STEPS,
  binds: [
    { name: "start_date", value: "2025-03-01", type: "date" },
    { name: "end_date", value: "2025-04-01", type: "date" },
  ],
  assumptions: ["Enrolled means ENROLLED = 1"], tables: ["IHS_2025.VFITBITDAILYDATA"], warnings: [],
  tables_named: ["IHS_2025.VFITBITDAILYDATA"], knowledge: ["tables/fitbit-daily"], tables_described: ["IHS_2025.VPARTICIPANTS"],
  kb_read: [], queries: [{ id: "q_1", status: "succeeded", tables: ["IHS_2025.VFITBITDAILYDATA"], row_count: 1, started_at: "" }],
  ...extra,
}); // prettier-ignore

const passes = (binds: string[]): SqlCheck => ({ ok: true, errors: [], warnings: [], tables: [], binds });
const running: SqlRun = {
  id: "pgr_1", state: "running", sql: STEPS, started_at: "", finished_at: null, message: null, diagnostic: null,
  query_id: null, row_count: null, bytes_written: null, elapsed_seconds: null, columns: [], tables: [], warnings: [],
}; // prettier-ignore

beforeEach(() => {
  sessionStorage.clear();
  chatProps.mockReset();
  vi.mocked(api.conversations).mockReset().mockResolvedValue([{ id: "c1", mode: "sql", busy: false } as never]);
  vi.mocked(sqlApi.status).mockResolvedValue({
    available: true, database_configured: true, playground_id: "pg_0", preview_rows: 200, max_rows: 1000, max_bytes: 1,
  }); // prettier-ignore
  vi.mocked(sqlApi.check).mockReset().mockImplementation(async (sql) =>
    passes(sql.includes(":start_date") ? ["end_date", "start_date"] : []),
  );
  vi.mocked(sqlApi.catalog).mockResolvedValue([
    { schema_name: "IHS_2024", tables: [{ name: "VSLEEP", type: "VIEW", comment: "", primary_key: [], columns: [] }] },
    {
      schema_name: "IHS_2026",
      tables: [{ name: "VFITBITDAILYDATA", type: "VIEW", comment: "Fitbit daily", primary_key: [], columns: [] }],
    },
  ]);
  vi.mocked(sqlApi.search).mockReset().mockResolvedValue([]);
  vi.mocked(sqlApi.proposals).mockReset().mockResolvedValue([]);
  vi.mocked(sqlApi.run).mockReset().mockResolvedValue(running);
  vi.mocked(sqlApi.runStatus).mockReset().mockImplementation(() => new Promise(() => undefined));
});

/** The page, with a SQL drafting chat `c1` this tab has read up to event `seen`. */
function show({ draft = "", seen = 0, extra = {} }: { draft?: string; seen?: number; extra?: Record<string, unknown> } = {}) {
  sessionStorage.setItem("datalab:sql:draft", draft);
  sessionStorage.setItem("datalab:sql:chat", "c1");
  sessionStorage.setItem("datalab:sql:handled", JSON.stringify({ chat: "c1", seq: seen }));
  for (const [key, value] of Object.entries(extra)) sessionStorage.setItem(`datalab:sql:${key}`, JSON.stringify(value));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <SqlPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function editorText() {
  const content = await screen.findByRole("textbox", { name: "SQL query" });
  return EditorView.findFromDOM(content)!.state.doc.toString();
}

it("fills an empty editor with the proposed query and its bind values, and doesn't run it", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  show();
  await waitFor(async () => expect(await editorText()).toBe(STEPS));
  expect(await screen.findByRole("textbox", { name: /:start_date/ })).toHaveValue("2025-03-01");
  expect(screen.getByRole("textbox", { name: /:end_date/ })).toHaveValue("2025-04-01");
  expect(screen.queryByRole("status", { name: "The agent proposed a query" })).not.toBeInTheDocument();
  // Nothing runs until Run is clicked.
  expect(sqlApi.run).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^Run/ }));
  await waitFor(() =>
    expect(sqlApi.run).toHaveBeenCalledWith(STEPS, { start_date: "2025-03-01", end_date: "2025-04-01" }),
  );
  // Kept for a reload.
  expect(JSON.parse(sessionStorage.getItem("datalab:sql:binds")!)).toEqual({ start_date: "2025-03-01", end_date: "2025-04-01" });
});

it("offers the proposal beside the person's work: Use this query keeps the draft, Replace doesn't", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  show({ draft: MINE });
  const banner = await screen.findByRole("status", { name: "The agent proposed a query" });
  expect(within(banner).getByText("Daily Fitbit steps, March 2025")).toBeInTheDocument();
  expect(await editorText()).toBe(MINE);
  fireEvent.click(within(banner).getByRole("button", { name: "Use this query" }));
  await waitFor(async () => expect(await editorText()).toBe(STEPS));
  fireEvent.click(screen.getByRole("button", { name: /Back to your earlier draft/ }));
  await waitFor(async () => expect(await editorText()).toBe(MINE));
  expect(sqlApi.run).not.toHaveBeenCalled();
});

it("replaces the draft only when asked, and Dismiss leaves it", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  show({ draft: MINE });
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
  expect(screen.queryByRole("status", { name: "The agent proposed a query" })).not.toBeInTheDocument();
  expect(await editorText()).toBe(MINE);
});

it("Replace current draft swaps it, with nothing kept aside", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  show({ draft: MINE });
  fireEvent.click(await screen.findByRole("button", { name: "Replace current draft" }));
  await waitFor(async () => expect(await editorText()).toBe(STEPS));
  expect(screen.queryByRole("button", { name: /Back to your earlier draft/ })).not.toBeInTheDocument();
});

it("revises the agent's own untouched draft on a follow-up, but never edits made while it worked", async () => {
  const march = proposal();
  const april = proposal({ proposal_id: "sp_2", turn: 2, seq: 20, title: "April 1–15", request: "limit this to April 1–15" });
  april.binds = [
    { name: "start_date", value: "2025-04-01", type: "date" },
    { name: "end_date", value: "2025-04-16", type: "date" },
  ];
  vi.mocked(sqlApi.proposals).mockResolvedValue([march, april]);
  // Untouched since it was put there, and as it was when the follow-up was sent: revised in place.
  const binds = { start_date: "2025-03-01", end_date: "2025-04-01" };
  const origin = { proposal: march, insertedSql: STEPS, insertedBinds: binds };
  show({ draft: STEPS, seen: 9, extra: { origin, binds, snapshot: { sql: STEPS, binds } } });
  expect(await screen.findByRole("textbox", { name: /:start_date/ })).toHaveValue("2025-04-01");
  expect(screen.queryByRole("status", { name: "The agent proposed a query" })).not.toBeInTheDocument();
});

it("doesn't replace a draft edited after the message was sent", async () => {
  const march = proposal();
  const april = proposal({ proposal_id: "sp_2", turn: 2, seq: 20, title: "April 1–15" });
  vi.mocked(sqlApi.proposals).mockResolvedValue([march, april]);
  const edited = `${STEPS}\nORDER BY RECORD_DATE`;
  show({ draft: edited, seen: 9, extra: { origin: { proposal: march, insertedSql: STEPS }, snapshot: STEPS } });
  expect(await screen.findByRole("status", { name: "The agent proposed a query" })).toBeInTheDocument();
  expect(await editorText()).toBe(edited);
});

it("keeps the draft when the generation failed or was stopped", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([
    proposal({ turn_status: "interrupted" }),
    proposal({ proposal_id: "sp_2", seq: 12, turn: 2, turn_status: "failed" }),
  ]);
  show({ draft: MINE });
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem("datalab:sql:handled")!)).toEqual({ chat: "c1", seq: 12 }));
  expect(await editorText()).toBe(MINE);
  expect(screen.queryByRole("status", { name: "The agent proposed a query" })).not.toBeInTheDocument();
});

it("an empty editor stays empty after a stopped generation, and a running turn's proposal waits", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([
    proposal({ turn_status: "interrupted" }),
    proposal({ proposal_id: "sp_2", seq: 12, turn: 2, turn_status: "running", turn_done: false }),
  ]);
  show();
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem("datalab:sql:handled")!).seq).toBe(9));
  expect(await editorText()).toBe("");
});

it("what a chat proposed before this tab saw it isn't offered again", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  sessionStorage.setItem("datalab:sql:chat", "c1");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <SqlPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem("datalab:sql:handled") ?? "null")).toEqual({ chat: "c1", seq: 9 }));
  expect(await editorText()).toBe("");
});

it("says how the SQL was created, with links to the turn and its activity", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: /How this SQL was created/ }));
  const panel = screen.getByRole("region", { name: "How this SQL was created" });
  expect(within(panel).getByText("Daily Fitbit steps for enrolled 2025 participants in March")).toBeInTheDocument();
  expect(within(panel).getByText("Enrolled means ENROLLED = 1")).toBeInTheDocument();
  expect(within(panel).getByText(":start_date = 2025-03-01", { exact: false })).toBeInTheDocument();
  expect(within(panel).getAllByText("VFITBITDAILYDATA (2025)")).toHaveLength(2);
  expect(within(panel).getByText(/VPARTICIPANTS \(2025\)/)).toBeInTheDocument();
  expect(within(panel).getByText("tables/fitbit-daily")).toBeInTheDocument();
  expect(within(panel).getByText(/1 row$/)).toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: /activity and queries in the Workspace/ })).toHaveAttribute(
    "href",
    "/workspace/c1",
  );
  // The chat opens at the turn.
  const scrolled = vi.fn();
  Element.prototype.scrollIntoView = scrolled;
  fireEvent.click(within(panel).getByRole("button", { name: "Show this turn in the chat" }));
  await waitFor(() => expect(chatProps).toHaveBeenCalled());
  await waitFor(() => expect(scrolled).toHaveBeenCalled());
  // Edited: it says so.
  expect(within(panel).queryByText(/changed the SQL since/)).not.toBeInTheDocument();
});

it("words the chat for describing data, and opens it from beside the editor, snapshotting the draft on send", async () => {
  sessionStorage.setItem("datalab:sql:chat-open", "closed");
  show({ draft: MINE });
  expect(chatProps).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: /Generate SQL with the agent/ }));
  await waitFor(() => expect(chatProps).toHaveBeenCalled());
  const props = chatProps.mock.lastCall![0] as { mode: string; placeholder: string; sendLabel: string; onSending: () => void };
  expect(props).toMatchObject({ mode: "sql", placeholder: "Describe the data you want", sendLabel: "Generate SQL" });
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Your question or instruction" })));
  props.onSending();
  expect(JSON.parse(sessionStorage.getItem("datalab:sql:snapshot")!)).toEqual({ sql: MINE, binds: {} });
});

it("starts with every cohort folded, and still opens one by hand and searches", async () => {
  show();
  const folded = await screen.findByText("IHS_2026");
  const details = [...document.querySelectorAll("details")];
  expect(details).toHaveLength(2);
  expect(details.every((d) => !d.open)).toBe(true);
  fireEvent.click(folded);
  expect(folded.closest("details")!.open).toBe(true);
  vi.mocked(sqlApi.search).mockResolvedValue([
    { schema_name: "IHS_2026", name: "VFITBITDAILYDATA", type: "VIEW", comment: "", matching_columns: [] },
  ]);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search tables and columns" }), { target: { value: "fitbit" } });
  const results = await screen.findByRole("region", { name: "Search results" });
  expect(await within(results).findByText("VFITBITDAILYDATA")).toBeInTheDocument();
});

it("decides fill or offer as documented", () => {
  const d = (sql: string, x = "1") => ({ sql, binds: { x } });
  const inserted = (sql: string, x = "1") => ({ proposal: proposal(), insertedSql: sql, insertedBinds: { x } });
  expect(onArrival(d("  "), inserted("A"), d("B"))).toBe("fill");
  expect(onArrival(d("A"), inserted("A"), d("A"))).toBe("fill");
  expect(onArrival(d("A2"), inserted("A"), d("A"))).toBe("offer");
  expect(onArrival(d("A"), inserted("A"), d("A0"))).toBe("offer");
  expect(onArrival(d("mine"), null, null)).toBe("offer");
  // A bind value edited, before the message or while the agent worked.
  expect(onArrival(d("A", "2"), inserted("A"), d("A", "2"))).toBe("offer");
  expect(onArrival(d("A", "2"), inserted("A"), d("A"))).toBe("offer");
  // An origin kept from before bind values were compared: offered.
  expect(onArrival(d("A"), { proposal: proposal(), insertedSql: "A" }, null)).toBe("offer");
  const done = proposal({ seq: 5 });
  expect(nextProposal([done, proposal({ seq: 7 })], 0).proposal?.seq).toBe(7);
  expect(nextProposal([done], 5)).toEqual({ proposal: null, seq: 5 });
});

it("offers a follow-up's proposal when a bind value was edited, and sends the bind values with the SQL", async () => {
  const march = proposal();
  const april = proposal({ proposal_id: "sp_2", turn: 2, seq: 20, title: "April 1–15" });
  vi.mocked(sqlApi.proposals).mockResolvedValue([march, april]);
  const inserted = { start_date: "2025-03-01", end_date: "2025-04-01" };
  const edited = { ...inserted, end_date: "2025-03-15" };
  sessionStorage.setItem("datalab:sql:chat-open", "open");
  show({
    draft: STEPS,
    seen: 9,
    extra: { origin: { proposal: march, insertedSql: STEPS, insertedBinds: inserted }, binds: edited, snapshot: { sql: STEPS, binds: edited } },
  });
  expect(await screen.findByRole("status", { name: "The agent proposed a query" })).toBeInTheDocument();
  expect(await screen.findByRole("textbox", { name: /:end_date/ })).toHaveValue("2025-03-15");
  await waitFor(() => {
    const context = (chatProps.mock.lastCall![0] as { context: { text: string } }).context.text;
    expect(context).toContain(STEPS);
    expect(context).toContain("-- :end_date = 2025-03-15");
    expect(context).toContain("-- :start_date = 2025-03-01");
  });
});

it("forgets the waiting proposal when another chat is opened", async () => {
  vi.mocked(sqlApi.proposals).mockResolvedValue([proposal()]);
  sessionStorage.setItem("datalab:sql:chat-open", "open");
  show({ draft: MINE });
  expect(await screen.findByRole("status", { name: "The agent proposed a query" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  await waitFor(() => expect(screen.queryByRole("status", { name: "The agent proposed a query" })).not.toBeInTheDocument());
  expect(sessionStorage.getItem("datalab:sql:proposal")).toBeNull();
  expect(await editorText()).toBe(MINE);
});
