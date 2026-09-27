import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EditorView } from "@codemirror/view";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type QueryRecord } from "@/api/client";
import { ApiError } from "@/api/http";
import { type WorkflowDraft, type WorkflowSave, workflowsApi } from "@/api/workflows";

import { type DraftSource, SaveAsWorkflow, slug } from "./SaveAsWorkflow";

vi.mock("@/api/workflows", () => ({
  workflowsApi: {
    destinations: vi.fn(),
    draft: vi.fn(),
    checkDraft: vi.fn(),
    save: vi.fn(),
    saveStatus: vi.fn(),
  },
}));
vi.mock("@/api/client", () => ({ api: { dataAccessed: vi.fn() } }));

// jsdom has no layout. CodeMirror measures text; give it empty boxes.
for (const proto of [Range.prototype, Element.prototype]) {
  proto.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as DOMRectList;
}
Range.prototype.getBoundingClientRect = () => new DOMRect();

const TEXT = "schema_version: 1\nname: steps_by_device\nreads:\n  - IHS_2025.WEARABLE_DAILY\n";

const draft = (extra: Partial<WorkflowDraft> = {}): WorkflowDraft => ({
  text: TEXT,
  valid: true,
  problems: [],
  notes: [],
  target: {
    kind: "local",
    folder: "/data/workflows-local",
    message: "Practice DataLab keeps workflows on this computer, in /data/workflows-local. It isn't shared with the lab.",
  },
  findings: [],
  ...extra,
});

const saved = (extra: Partial<WorkflowSave> = {}): WorkflowSave => ({
  id: null, state: "saved", shared: false, path: "steps_by_device.yaml", message: "Saved on this computer.",
  commit: null, findings: [], test: null, ...extra,
}); // prettier-ignore

const record = (id: string, sql: string, status = "succeeded", at = "2026-09-27T10:00:00Z"): QueryRecord => ({
  id, started_at: at, status, sql_text: sql, tables: ["IHS_2025.WEARABLE_DAILY"], row_count: 3,
  elapsed_ms: 5, result_file: null, message: null,
}); // prettier-ignore

beforeEach(() => {
  vi.mocked(workflowsApi.destinations).mockReset().mockResolvedValue([]);
  vi.mocked(workflowsApi.draft).mockReset().mockResolvedValue(draft());
  vi.mocked(workflowsApi.checkDraft).mockReset();
  vi.mocked(workflowsApi.save).mockReset().mockResolvedValue(saved());
  vi.mocked(workflowsApi.saveStatus).mockReset();
  vi.mocked(api.dataAccessed).mockReset().mockResolvedValue([]);
});

const PLAYGROUND: DraftSource = { kind: "playground", sql: "SELECT DEVICE FROM IHS_2025.WEARABLE_DAILY WHERE D >= :d", binds: { d: "2025-04-01" } };

function show(source: DraftSource = PLAYGROUND, suggestedName = "WEARABLE_DAILY") {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <SaveAsWorkflow source={source} suggestedName={suggestedName} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return onClose;
}

it("drafts from the Playground's query and its binds, and saves it locally, saying it isn't shared", async () => {
  show();
  const name = screen.getByRole("textbox", { name: /Name/ });
  expect(name).toHaveValue("wearable_daily");
  fireEvent.change(name, { target: { value: "Steps By Device" } });
  expect(name).toHaveValue("steps_by_device");
  fireEvent.change(screen.getByRole("combobox", { name: /Deliver to/ }), { target: { value: "practice-folder" } });
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  await screen.findByRole("textbox", { name: "The drafted workflow file" });
  expect(workflowsApi.draft).toHaveBeenCalledWith({
    name: "steps_by_device",
    description: "",
    destination: "practice-folder",
    queries: [{ sql: PLAYGROUND.kind === "playground" ? PLAYGROUND.sql : "", binds: { d: "2025-04-01" } }],
  });
  // Said once.
  expect(screen.getByText(/Practice DataLab keeps workflows/).textContent?.match(/shared/g)).toHaveLength(1);
  expect(workflowsApi.save).not.toHaveBeenCalled(); // drafting saves nothing
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Saved.")).toBeInTheDocument();
  expect(workflowsApi.save).toHaveBeenCalledWith({ text: TEXT, confirmed: [], source: "playground", conversation_id: null });
  expect(screen.getByRole("link", { name: /Open it in Workflows/ })).toHaveAttribute(
    "href",
    "/workflows/file?path=steps_by_device.yaml",
  );
});

it("says why a query the SQL check refuses can't be drafted", async () => {
  vi.mocked(workflowsApi.draft).mockRejectedValue(new ApiError(422, "The SQL check refuses it: Only SELECT queries are allowed."));
  show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The SQL check refuses it");
});

it("won't save a draft with problems, and lists them with where they are", async () => {
  vi.mocked(workflowsApi.draft).mockResolvedValue(
    draft({
      valid: false,
      notes: ["The query aggregates, but DataLab couldn't tell which columns are counts."],
      problems: [{ path: "steps[1].qc.small_cells.count_columns", message: "Name the count columns.", line: 9, column: 7 }],
    }),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  expect(await screen.findByText("Name the count columns.")).toBeInTheDocument();
  expect(screen.getByText(/couldn't tell which columns are counts/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

const FINDING = {
  id: "f1", path: "workflows/steps_by_device.yaml", rule: "study_id", severity: "data" as const,
  message: "This looks like a participant or study ID.", line: 12, text: "AND STUDY_PARTICIPANT_ID <> 'SYN-0042'",
}; // prettier-ignore

async function editor() {
  const content = await screen.findByRole("textbox", { name: "The drafted workflow file" });
  return EditorView.findFromDOM(content)!;
}

it("checks the file again after an edit, and won't save it until it passes", async () => {
  vi.mocked(workflowsApi.checkDraft).mockResolvedValue({
    valid: false,
    problems: [{ path: "steps[1].qc.small_cells.min", message: "At least 11: counts from 1 to 10 are small.", line: 4, column: 3 }],
    findings: [],
  });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  const view = await editor();
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeEnabled();
  view.dispatch({ changes: { from: view.state.doc.length, insert: "# lowered\n" } });
  await waitFor(() => expect(save).toBeDisabled()); // while it's checked again, too
  expect(await screen.findByText("At least 11: counts from 1 to 10 are small.")).toBeInTheDocument();
  expect(workflowsApi.checkDraft).toHaveBeenCalledWith(`${TEXT}# lowered\n`);
  expect(save).toBeDisabled();
});

it("asks for each possible-data finding, and one edited away stops blocking", async () => {
  vi.mocked(workflowsApi.draft).mockResolvedValue(draft({ findings: [FINDING] }));
  vi.mocked(workflowsApi.checkDraft).mockResolvedValue({ valid: true, problems: [], findings: [] });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  expect(await screen.findByText(FINDING.text)).toBeInTheDocument();
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /isn't participant data/ }));
  expect(save).toBeEnabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /isn't participant data/ }));
  const view = await editor();
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "name: steps_by_device\n" } });
  await waitFor(() => expect(screen.queryByText(FINDING.text)).not.toBeInTheDocument());
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);
  await screen.findByText("Saved.");
  expect(vi.mocked(workflowsApi.save).mock.calls[0][0]).toMatchObject({ text: "name: steps_by_device\n" });
});

it("doesn't throw away an edited or saving review on Escape", async () => {
  const onClose = show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  const view = await editor();
  vi.mocked(workflowsApi.checkDraft).mockResolvedValue({ valid: true, problems: [], findings: [] });
  view.dispatch({ changes: { from: 0, insert: "# mine\n" } });
  await waitFor(() => expect(workflowsApi.checkDraft).toHaveBeenCalled());
  const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(ask).toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
  // While a Save & share goes, Escape does nothing at all.
  vi.mocked(workflowsApi.save).mockResolvedValue(saved({ id: "ws_1", state: "saving", shared: true }));
  vi.mocked(workflowsApi.saveStatus).mockReturnValue(new Promise(() => {}));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("status");
  ask.mockReset().mockReturnValue(true);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(ask).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
  ask.mockRestore();
});

it("says so when someone else saved the same file meanwhile, rather than that it was shared", async () => {
  vi.mocked(workflowsApi.draft).mockResolvedValue(draft({ target: { kind: "share", folder: null, message: "Shared." } }));
  vi.mocked(workflowsApi.save).mockResolvedValue(
    saved({ id: "ws_1", state: "already_there", shared: true, path: "workflows/steps_by_device.yaml",
      message: "workflows/steps_by_device.yaml was already in the pipelines repo. Nothing new was shared." }),
  ); // prettier-ignore
  show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save & share" }));
  expect(await screen.findByText(/Nothing new was shared/)).toBeInTheDocument();
  expect(screen.queryByText(/Saved and shared/)).not.toBeInTheDocument();
  expect(within(screen.getByRole("dialog")).getByRole("link", { name: /Open theirs/ })).toBeInTheDocument();
});

it("shares through Save & share, and waits for each possible-data finding to be confirmed", async () => {
  vi.mocked(workflowsApi.draft).mockResolvedValue(
    draft({ target: { kind: "share", folder: null, message: "Save & share checks it, runs the tests and pushes it." } }),
  );
  const finding = FINDING;
  vi.mocked(workflowsApi.save)
    .mockResolvedValueOnce(saved({ id: "ws_1", state: "saving", shared: true, path: "workflows/steps_by_device.yaml" }))
    .mockResolvedValueOnce(saved({ id: "ws_2", state: "saving", shared: true, path: "workflows/steps_by_device.yaml" }));
  vi.mocked(workflowsApi.saveStatus).mockImplementation(async (id) =>
    id === "ws_1"
      ? saved({ id, state: "check_failed", shared: true, findings: [finding], message: "Confirm each one isn't." })
      : saved({ id, state: "saved", shared: true, path: "workflows/steps_by_device.yaml", commit: "abcdef0123456789" }),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save & share" }));
  expect(await screen.findByText("AND STUDY_PARTICIPANT_ID <> 'SYN-0042'", {}, { timeout: 3000 })).toBeInTheDocument();
  const again = screen.getByRole("button", { name: "Save & share again" });
  expect(again).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /isn't participant data/ }));
  expect(again).toBeEnabled();
  fireEvent.click(again);
  expect(await screen.findByText("Saved and shared with the lab.", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(vi.mocked(workflowsApi.save).mock.calls[1][0].confirmed).toEqual(["f1"]);
  expect(screen.getByText("abcdef0123")).toBeInTheDocument();
});

it("turns a conversation's queries that ran into a draft, each SQL once, as the person picks them", async () => {
  vi.mocked(api.dataAccessed).mockResolvedValue([
    record("q_1", "SELECT A FROM IHS_2025.WEARABLE_DAILY", "succeeded", "2026-09-27T10:00:00Z"),
    record("q_2", "SELECT NOPE FROM IHS_2025.WEARABLE_DAILY", "rejected", "2026-09-27T10:01:00Z"),
    record("q_3", "SELECT B FROM IHS_2025.WEARABLE_DAILY", "succeeded", "2026-09-27T10:02:00Z"),
    record("q_4", "SELECT A FROM IHS_2025.WEARABLE_DAILY", "succeeded", "2026-09-27T10:03:00Z"),
  ]);
  show({ kind: "conversation", conversationId: "c_1" }, "");
  expect(await screen.findByText("SELECT B FROM IHS_2025.WEARABLE_DAILY")).toBeInTheDocument();
  expect(screen.queryByText(/NOPE/)).not.toBeInTheDocument();
  const boxes = screen.getAllByRole("checkbox");
  expect(boxes).toHaveLength(2);
  fireEvent.click(boxes[0]); // q_3 (the SELECT A query's latest run, q_4, comes after it)
  fireEvent.change(screen.getByRole("textbox", { name: /Name/ }), { target: { value: "from_chat" } });
  fireEvent.click(screen.getByRole("button", { name: "Draft the workflow" }));
  await waitFor(() => expect(workflowsApi.draft).toHaveBeenCalled());
  expect(vi.mocked(workflowsApi.draft).mock.calls[0][0]).toMatchObject({ conversation_id: "c_1", query_ids: ["q_4"] });
  await screen.findByRole("textbox", { name: "The drafted workflow file" });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved.");
  expect(vi.mocked(workflowsApi.save).mock.calls[0][0]).toMatchObject({ source: "conversation", conversation_id: "c_1" });
});

it("makes names the file check takes", () => {
  expect(slug("Fitbit Daily 2025.v2")).toBe("fitbit_daily_2025_v2");
  expect(slug("IHS_2025/étude")).toBe("ihs_2025tude");
});
