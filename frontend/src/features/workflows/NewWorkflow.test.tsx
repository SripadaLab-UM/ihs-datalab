import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import type { Stages, StagesResult, Workflow } from "@/api/workflows";
import { workflowsApi } from "@/api/workflows";
import { EmptyState } from "@/components/chat/Chat";

import { EXAMPLE } from "./NewWorkflow";
import { WorkflowsPage } from "./WorkflowsPage";

vi.mock("@/api/workflows", () => ({
  workflowsApi: {
    status: vi.fn(),
    list: vi.fn(),
    text: vi.fn(),
    runs: vi.fn(async () => []),
    run: vi.fn(),
    destinations: vi.fn(async () => []),
    stages: vi.fn(),
    testRun: vi.fn(),
    save: vi.fn(),
    saveStatus: vi.fn(),
  },
  runStreamUrl: (id: string) => `/stream/${id}`,
}));
vi.mock("@/api/pipelines", () => ({ pipelinesApi: { proposals: vi.fn(async () => []), proposal: vi.fn() } }));
vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "practice" })),
    conversations: vi.fn(async () => []),
    createConversation: vi.fn(),
    send: vi.fn(),
    files: vi.fn(async () => []),
    fileText: vi.fn(),
    modes: vi.fn(async () => []),
  },
}));
const chatProps = vi.fn();
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: (props: unknown) => {
    chatProps(props);
    return <div>chat</div>;
  },
}));
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: (props: { value: string; label: string; onChange?: (v: string) => void }) => (
    <textarea aria-label={props.label} value={props.value} onChange={(e) => props.onChange?.(e.target.value)} />
  ),
}));

const LOCAL = { kind: "local" as const, folder: "/p/workflows-local", message: "Practice DataLab keeps workflows on this computer." };
const DRAFT = "name: fitbit_daily_clean\nsteps: []\n";

const stages = (extra: Partial<Stages> = {}): Stages => ({
  name: "fitbit_daily_clean",
  description: "Fitbit daily rows, body composition removed.",
  parameters: [{ name: "start_date", type: "date", default: "2025-04-01", description: "First day" }],
  reads: ["IHS_2025.VFITBITDAILYDATA"],
  extract: [{ id: "extract", description: "Fitbit daily rows", sql: "SELECT * FROM IHS_2025.VFITBITDAILYDATA", output: "raw.csv", tables: ["IHS_2025.VFITBITDAILYDATA"] }],
  process: [
    { id: "drop_body", kind: "r", description: "Remove body composition", inputs: { raw: "extract" }, outputs: { final: "clean.csv" }, script: "# DataLab: drop columns\n", drop_columns: ["BODYBMI", "BODYFAT"], pipeline: null, file: null, min_rows: null, max_rows: null, required_columns: [], no_missing: [], unique_by: null, small_cells: null, other_rules: [] },
    { id: "check_days", kind: "check", description: "", inputs: {}, outputs: {}, script: null, drop_columns: null, pipeline: null, file: "drop_body", min_rows: 1, max_rows: null, required_columns: [], no_missing: [], unique_by: ["PARTICIPANTIDENTIFIER", "RECORD_DATE"], small_cells: null, other_rules: [] },
  ],
  deliver: { destination: "practice-exports", folder: "fitbit_daily_clean", files: ["drop_body"], without_small_cells: {} },
  outputs: [
    { ref: "extract", step: "extract", file: "raw.csv" },
    { ref: "drop_body", step: "drop_body", file: "clean.csv" },
  ],
  ...extra,
}); // prettier-ignore

const result = (extra: Partial<StagesResult> = {}): StagesResult => ({
  text: DRAFT,
  stages: stages(),
  valid: true,
  problems: [],
  findings: [],
  target: LOCAL,
  destinations: [{ key: "practice-exports", name: "Practice exports", path: "/p/practice-exports", available: true, destination_id: null, mapped: true }],
  ...extra,
});

const workflow = (extra: Partial<Workflow> = {}): Workflow => ({
  path: "builtin/fitbit_daily_2025.yaml", name: "fitbit_daily_2025", description: "", valid: true, problems: [], parameters: [],
  steps: [], reads: [], deliver: null, source: "file", blob: "sha256:a", commit: null, last_run: null, builtin: true,
  ...extra,
}); // prettier-ignore

beforeEach(() => {
  sessionStorage.clear();
  chatProps.mockReset();
  vi.mocked(workflowsApi.status).mockResolvedValue({ available: true, folder: "/p/workflows-local", profile: "practice", target: LOCAL });
  vi.mocked(workflowsApi.list).mockReset().mockResolvedValue([]);
  vi.mocked(workflowsApi.stages).mockReset().mockResolvedValue(result());
  vi.mocked(workflowsApi.testRun).mockReset();
  vi.mocked(workflowsApi.save).mockReset();
  vi.mocked(api.files).mockReset().mockResolvedValue([]);
  vi.mocked(api.fileText).mockReset();
  vi.mocked(api.createConversation).mockReset().mockResolvedValue({ id: "conv_1" } as never);
  vi.mocked(api.send).mockReset().mockResolvedValue({ id: "conv_1" } as never);
  vi.mocked(api.conversations).mockReset().mockResolvedValue([]);
});

function show(at: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="workflows/*" element={<WorkflowsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("puts New workflow in the middle when there are none, with the three stages and no folder path", async () => {
  show("/workflows");
  const first = await screen.findByRole("region", { name: "Make your first workflow" });
  expect(within(first).getByRole("link", { name: /New workflow/ })).toHaveAttribute("href", "/workflows/new");
  const explained = within(first).getByRole("list", { name: "How a workflow is built" });
  expect(within(explained).getAllByRole("listitem").map((li) => li.querySelector(".font-medium")?.textContent)).toEqual([
    "Extract",
    "Process & QC",
    "Deliver",
  ]);
  expect(screen.queryByText(/No workflows yet/)).not.toBeInTheDocument();
  expect(screen.queryByText("/p/workflows-local")).not.toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent(/practice-only: it runs on synthetic data/);
});

it("lists workflows with New workflow at the top, and marks the built-in ones", async () => {
  vi.mocked(workflowsApi.list).mockResolvedValue([workflow(), workflow({ path: "mine.yaml", name: "mine", builtin: false })]);
  show("/workflows");
  const list = await screen.findByRole("list", { name: "Workflows" });
  expect(screen.getAllByRole("link", { name: /New workflow/ })[0]).toHaveAttribute("href", "/workflows/new");
  const [builtin, mine] = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
  expect(within(builtin).getByText("built-in")).toBeInTheDocument();
  expect(within(mine).queryByText("built-in")).not.toBeInTheDocument();
});

it("asks what the workflow should do, and starts the authoring chat with the description", async () => {
  show("/workflows/new");
  const box = await screen.findByRole("textbox", { name: /What should this workflow do\?/ });
  expect(box).toHaveAttribute("placeholder", EXAMPLE);
  fireEvent.change(box, { target: { value: EXAMPLE } });
  fireEvent.click(screen.getByRole("button", { name: /Start drafting/ }));
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("conv_1", EXAMPLE));
  expect(api.createConversation).toHaveBeenCalledWith("workflows");
  // The chat opens on that conversation, and the panel waits for its draft.
  await waitFor(() => expect(chatProps).toHaveBeenLastCalledWith(expect.objectContaining({ conversationId: "conv_1", mode: "workflows" })));
  expect(await screen.findByText(/working out the details/)).toBeInTheDocument();
});

it("shows the assistant's draft as three editable stages, and edits go to the backend", async () => {
  sessionStorage.setItem("datalab:workflows:chat", "conv_1");
  vi.mocked(api.conversations).mockResolvedValue([{ id: "conv_1" }] as never);
  vi.mocked(api.files).mockResolvedValue([
    { path: "fitbit_daily_clean.yaml", size: 10, modified: "2026-09-27T12:00:00Z", kind: "text", checkpoint: 3 },
  ]);
  vi.mocked(api.fileText).mockResolvedValue({ text: DRAFT, truncated: false });
  show("/workflows/new");
  const cards = await screen.findByRole("list", { name: "Stages" });
  const [extract, process, deliver] = within(cards).getAllByRole("listitem").filter((li) => li.parentElement === cards);
  expect(within(extract).getByRole("heading", { name: "Extract" })).toBeInTheDocument();
  expect(within(extract).getByText("IHS_2025.VFITBITDAILYDATA")).toBeInTheDocument();
  expect(within(process).getByRole("heading", { name: "Process & QC" })).toBeInTheDocument();
  expect(within(deliver).getByRole("heading", { name: "Deliver" })).toBeInTheDocument();
  expect(api.fileText).toHaveBeenCalledWith("conv_1", "outputs", "fitbit_daily_clean.yaml", 3);
  expect(workflowsApi.stages).toHaveBeenCalledWith({ text: DRAFT });

  // The columns a drop step removes, edited as a list.
  const columns = within(process).getByRole("textbox", { name: "Columns it removes" });
  expect(columns).toHaveValue("BODYBMI, BODYFAT");
  vi.mocked(workflowsApi.stages).mockResolvedValue(result({ text: DRAFT + "# edited\n" }));
  fireEvent.change(columns, { target: { value: "BODYBMI, BODYFAT, WATER" } });
  fireEvent.blur(columns);
  await waitFor(() =>
    expect(workflowsApi.stages).toHaveBeenLastCalledWith({
      text: DRAFT,
      edits: { steps: { drop_body: { drop_columns: ["BODYBMI", "BODYFAT", "WATER"] } } },
      map_destination: null,
    }),
  );
  // The duplicate participant-days check, and a parameter's default.
  expect(within(process).getByRole("textbox", { name: "One row per (no duplicates of)" })).toHaveValue(
    "PARTICIPANTIDENTIFIER, RECORD_DATE",
  );
  const start = within(extract).getByLabelText("Default for start_date");
  fireEvent.change(start, { target: { value: "2025-05-01" } });
  fireEvent.blur(start);
  await waitFor(() =>
    expect(workflowsApi.stages).toHaveBeenLastCalledWith(
      expect.objectContaining({ edits: { parameters: { start_date: { default: "2025-05-01" } } } }),
    ),
  );
  // Deliver: the export folder, and where it is.
  expect(within(deliver).getByRole("combobox", { name: "Export folder" })).toHaveValue("practice-exports");
  expect(within(deliver).getByText("/p/practice-exports")).toBeInTheDocument();
});

it("keeps YAML behind an Advanced toggle, and checks it after an edit", async () => {
  sessionStorage.setItem("datalab:workflows:draft", DRAFT);
  show("/workflows/new");
  await screen.findByRole("list", { name: "Stages" });
  expect(screen.queryByRole("textbox", { name: "The workflow file (YAML)" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Advanced: YAML/ }));
  const editor = screen.getByRole("textbox", { name: "The workflow file (YAML)" });
  expect(editor).toHaveValue(DRAFT);
  const edited = DRAFT.replace("steps: []", "steps: [x]");
  vi.mocked(workflowsApi.stages).mockResolvedValue(
    result({ text: edited, stages: null, valid: false, problems: [{ path: "steps[0]", message: "Not a step.", line: 2, column: 9 }] }),
  );
  fireEvent.change(editor, { target: { value: edited } });
  await waitFor(() => expect(workflowsApi.stages).toHaveBeenLastCalledWith({ text: edited }), { timeout: 2000 });
  expect(await screen.findByText("Not a step.")).toBeInTheDocument();
  expect(screen.getByText(/can't show this draft as stages/)).toBeInTheDocument();
});

it("says there are no export folders yet, and where to add one", async () => {
  sessionStorage.setItem("datalab:workflows:draft", DRAFT);
  vi.mocked(workflowsApi.status).mockResolvedValue({ available: true, folder: "/r", profile: "real", target: LOCAL });
  vi.mocked(workflowsApi.stages).mockResolvedValue(result({ destinations: [] }));
  show("/workflows/new");
  const status = await screen.findByText(/No export folders are set up yet/);
  expect(within(status).getByRole("link", { name: "Settings → Export folders" })).toHaveAttribute("href", "/settings");
  // Not practice: no test run on this computer.
  expect(screen.queryByRole("button", { name: /Test run/ })).not.toBeInTheDocument();
  expect(screen.getByText(/Test runs of a draft happen on synthetic data, in Practice DataLab/)).toBeInTheDocument();
});

it("test-runs the draft on practice data, then saves it locally", async () => {
  sessionStorage.setItem("datalab:workflows:draft", DRAFT);
  sessionStorage.setItem("datalab:workflows:chat", "conv_1");
  vi.mocked(api.conversations).mockResolvedValue([{ id: "conv_1" }] as never);
  vi.mocked(workflowsApi.testRun).mockResolvedValue({ id: "run_t" } as never);
  vi.mocked(workflowsApi.run).mockResolvedValue({
    id: "run_t", status: "succeeded", finished_at: "x", steps: [], message: null,
    delivery_status: "skipped", delivery_message: "A test run doesn't deliver.",
  } as never); // prettier-ignore
  vi.mocked(workflowsApi.save).mockResolvedValue({
    id: null, state: "saved", shared: false, path: "fitbit_daily_clean.yaml", message: "Saved on this computer.", findings: [],
  } as never); // prettier-ignore
  show("/workflows/new");
  await screen.findByText(/Passes DataLab's workflow check/);
  fireEvent.click(screen.getByRole("button", { name: "Test run on practice data" }));
  await waitFor(() => expect(workflowsApi.testRun).toHaveBeenCalledWith(DRAFT));
  expect(await screen.findByText("A test run doesn't deliver.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open the test run" })).toHaveAttribute("href", "/workflows/runs/run_t");

  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(workflowsApi.save).toHaveBeenCalledWith({ text: DRAFT, confirmed: [], source: "authoring", conversation_id: "conv_1" }),
  );
  expect(await screen.findByText(/Saved in Practice DataLab's workflows, on this computer/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open it to run/ })).toHaveAttribute("href", "/workflows/file?path=fitbit_daily_clean.yaml");
});

it("asks Workflow authoring's own question in an empty chat", async () => {
  vi.mocked(api.modes).mockResolvedValue([
    { id: "workflows", label: "Workflow authoring", kind: "data", description: "", starters: [], tab_only: true, queries: true, attachments: true, question: "What should this workflow do?" },
  ]); // prettier-ignore
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EmptyState mode="workflows" kind="data" onPick={() => undefined} starting={false} />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("heading", { name: "What should this workflow do?" })).toBeInTheDocument();
  expect(screen.queryByText("What would you like to find out?")).not.toBeInTheDocument();
});
