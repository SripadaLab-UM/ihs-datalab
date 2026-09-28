import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { ApiError } from "@/api/http";
import { pipelinesApi } from "@/api/pipelines";
import type { RunDetail, RunStep, Workflow, WorkflowRun } from "@/api/workflows";
import { workflowsApi } from "@/api/workflows";

import { WorkflowsPage } from "./WorkflowsPage";

vi.mock("@/api/workflows", () => ({
  workflowsApi: {
    status: vi.fn(),
    list: vi.fn(),
    text: vi.fn(),
    runs: vi.fn(),
    run: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    again: vi.fn(),
    replayCheck: vi.fn(),
    replay: vi.fn(),
    destinations: vi.fn(),
  },
  runStreamUrl: (id: string) => `/stream/${id}`,
}));
vi.mock("@/api/pipelines", () => ({ pipelinesApi: { proposals: vi.fn(async () => []) } }));
vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "practice" })),
    conversations: vi.fn(async () => []),
  },
}));
// The chat and the editor have their own tests: here, only what the page gives them.
const chatProps = vi.fn();
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: (props: unknown) => {
    chatProps(props);
    return <div>chat</div>;
  },
}));
const editorProps = vi.fn();
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: (props: { value: string; label: string }) => {
    editorProps(props);
    return <pre aria-label={props.label}>{props.value}</pre>;
  },
}));

// jsdom has no EventSource: a stand-in the test can speak through.
class FakeEventSource {
  static readonly CLOSED = 2;
  static last: FakeEventSource | null = null;
  listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {};
  closed = false;
  readyState = 1;
  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, listener: (e: MessageEvent<string>) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    for (const listener of this.listeners[type] ?? []) listener({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

const YAML = "name: weekly_steps\nsteps:\n  - id: extract\n";

const workflow = (extra: Partial<Workflow> = {}): Workflow => ({
  path: "weekly_steps.yaml", name: "weekly_steps", description: "Weekly steps by device.", valid: true, problems: [],
  parameters: [
    { name: "start_date", type: "date", default: "2025-04-01", description: "" },
    { name: "min_cell", type: "integer", default: 11, description: "The smallest count shown." },
    { name: "suppress", type: "boolean", default: true, description: "" },
  ],
  steps: [
    { id: "extract", kind: "sql", description: "", inputs: {}, outputs: { final: "daily.csv" } },
    { id: "check", kind: "qc_builtin", description: "", inputs: {}, outputs: {} },
  ],
  reads: ["IHS_2025.WEARABLE_DAILY"],
  deliver: { destination: "practice-folder", folder: "weekly_steps", files: ["summary"] },
  source: "file", blob: "sha256:abc", commit: null, last_run: null, builtin: false,
  ...extra,
}); // prettier-ignore

const step = (step_id: string, kind: RunStep["kind"], status: RunStep["status"], extra: Partial<RunStep> = {}): RunStep => ({
  step_id, position: 0, kind, status, started_at: null, finished_at: null, seed: null, query_id: null, sql_text: null,
  binds: null, queries: [], exit_code: null, elapsed_ms: null, inputs: {}, outputs: {}, result: null, message: null,
  ...extra,
}); // prettier-ignore

const summary = (extra: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: "run_1", workflow_name: "weekly_steps", workflow_path: "weekly_steps.yaml", mode: "run", of_run: null,
  status: "succeeded", started_at: "2026-09-27T12:00:00Z", finished_at: "2026-09-27T12:01:00Z", started_by: "yu",
  message: null, delivery_status: "delivered", delivery_message: "Saved to Lab Dropbox (on this computer): 1 file. Dropbox will upload it when its app is running and signed in. DataLab can't confirm the upload.", replay_exact: null,
  replay_notes: [], reproduced: null,
  ...extra,
}); // prettier-ignore

const detail = (extra: Partial<RunDetail> = {}): RunDetail => ({
  ...summary(),
  workflow_source: "file", repo_commit: null, workflow_blob: "sha256:abc", image_ref: "datalab-agent:dev",
  image_digest: "sha256:1234567890abcdef", image_platform: "linux/arm64", host_platform: "linux/arm64",
  r_packages_sha256: "fedcba", runner_version: "datalab 0.1; wrapper sha256:x", runtime: {},
  params: { start_date: "2025-04-01", min_cell: 11 }, seed: 42, reads: ["IHS_2025.WEARABLE_DAILY"], pipelines: [],
  run_dir: "runs/run_1", inputs_kept: true,
  steps: [
    step("extract", "sql", "succeeded", {
      sql_text: "SELECT 1 FROM IHS_2025.WEARABLE_DAILY", binds: { start_date: "2025-04-01" },
      outputs: { final: { file: "daily.csv", sha256: "aaaabbbbccccdddd", rows: 120, columns: ["A", "B"] } },
    }),
    step("check", "qc_builtin", "succeeded", {
      result: { status: "ok", checks: [{ id: "min_rows", status: "pass", observed: 120, expected: ">= 1", message: "120 rows" }] },
    }),
  ],
  deliveries: [
    { id: "dl_1", destination_key: "practice-folder", destination_path: "/x", folder: "/x/weekly_steps/2026-09-27",
      files: [{ path: "weekly.csv", bytes: 300, sha256: "eeeeffff00001111" }], manifest_sha256: "99998888", delivered_at: "2026-09-27T12:01:00Z",
      destination_name: "Lab Dropbox", sync_provider: "dropbox", saved_to: "Saved to Lab Dropbox (on this computer)",
      sync_note: "Dropbox will upload it when its app is running and signed in. DataLab can't confirm the upload." },
  ],
  ...extra,
}); // prettier-ignore

beforeEach(() => {
  sessionStorage.clear();
  chatProps.mockReset();
  editorProps.mockReset();
  FakeEventSource.last = null;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.mocked(workflowsApi.status).mockResolvedValue({ available: true, folder: "/data/workflows-local", profile: "practice" });
  vi.mocked(workflowsApi.list).mockReset().mockResolvedValue([workflow()]);
  vi.mocked(workflowsApi.text).mockReset().mockResolvedValue({ path: "weekly_steps.yaml", text: YAML, source: "file", blob: "sha256:abc", commit: null });
  vi.mocked(workflowsApi.runs).mockReset().mockResolvedValue([]);
  vi.mocked(workflowsApi.run).mockReset().mockResolvedValue(detail());
  vi.mocked(workflowsApi.destinations).mockReset().mockResolvedValue([
    { key: "practice-folder", used_by: ["weekly_steps.yaml"], destination_id: null, name: "Practice exports", path: "/p", available: true },
  ]);
  for (const call of [workflowsApi.start, workflowsApi.stop, workflowsApi.again, workflowsApi.replay, workflowsApi.replayCheck]) {
    vi.mocked(call).mockReset();
  }
});

afterEach(() => vi.unstubAllGlobals());

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

const FILE = "/workflows/file?path=weekly_steps.yaml";

it("lists the workflows with their checks, problems by path, and last run", async () => {
  vi.mocked(workflowsApi.list).mockResolvedValue([
    workflow({ last_run: summary({ status: "failed", delivery_status: "skipped", delivery_message: "A step failed." }) }),
    workflow({
      path: "broken.yaml", name: null, valid: false, parameters: [], steps: [], deliver: null,
      problems: [{ path: "steps[1].inputs.raw", message: "'nope' isn't an earlier step.", line: 12, column: 7 }],
    }),
  ]); // prettier-ignore
  show("/workflows");
  const list = await screen.findByRole("list", { name: "Workflows" });
  const [ok, broken] = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
  expect(within(ok).getByText("ready to run")).toBeInTheDocument();
  expect(within(ok).getByText("failed")).toBeInTheDocument();
  expect(within(ok).getByText("not delivered")).toBeInTheDocument();
  expect(within(broken).getByText("1 problem")).toBeInTheDocument();
  expect(within(broken).getByText("line 12 · steps[1].inputs.raw")).toBeInTheDocument();
  expect(within(broken).getByText("'nope' isn't an earlier step.")).toBeInTheDocument();
  expect(within(broken).getByText("Not run yet.")).toBeInTheDocument();
  // Destinations, read-only.
  expect(await screen.findByText("practice-folder")).toBeInTheDocument();
  expect(screen.getByText(/delivers only to its own practice folder/)).toBeInTheDocument();
});

it("shows a file's problems against their paths, as marks in the read-only editor", async () => {
  vi.mocked(workflowsApi.list).mockResolvedValue([
    workflow({
      valid: false, parameters: [], steps: [], deliver: null,
      problems: [
        { path: "steps[0].sql", message: "IHS_2025.X isn't in reads.", line: 3, column: 5 },
        { path: "reads", message: "This is required.", line: null, column: null },
      ],
    }),
  ]); // prettier-ignore
  show(FILE);
  expect(await screen.findByText("Why it can't run")).toBeInTheDocument();
  expect(screen.getByText("line 3 · steps[0].sql")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
  await waitFor(() => expect(editorProps).toHaveBeenLastCalledWith(expect.objectContaining({ value: YAML, readOnly: true })));
  expect(editorProps.mock.lastCall![0].diagnostics).toEqual([
    { line: 3, column: 5, message: "IHS_2025.X isn't in reads.", severity: "error" },
    { line: 1, column: undefined, message: "reads: This is required.", severity: "error" },
  ]);
});

it("doesn't mark a file that changed since it was checked", async () => {
  vi.mocked(workflowsApi.list).mockResolvedValue([
    workflow({ valid: false, problems: [{ path: "steps[0].sql", message: "Bad.", line: 3, column: 5 }] }),
  ]);
  vi.mocked(workflowsApi.text).mockResolvedValue({ path: "weekly_steps.yaml", text: YAML, source: "file", blob: "sha256:new", commit: null });
  show(FILE);
  expect(await screen.findByText(/changed since it was checked/)).toBeInTheDocument();
  expect(editorProps.mock.lastCall![0].diagnostics).toBeUndefined();
});

it("says plainly when delivery is blocked waiting for a small-cells check or a reason", async () => {
  const blocked = {
    path: "deliver.files[0]",
    message: "A delivered CSV needs a small_cells check first (or a reason under deliver.without_small_cells).",
    line: 20,
    column: 11,
  };
  vi.mocked(workflowsApi.list).mockResolvedValue([workflow({ valid: false, problems: [blocked] })]);
  show("/workflows");
  expect(await screen.findByText("delivery blocked")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "weekly_steps", hidden: false, current: false }));
  const alert = await screen.findByText("Delivery is blocked, so this workflow can't run yet.");
  expect(within(alert.parentElement!).getByText(/needs a small_cells check first/)).toBeInTheDocument();
});

it("runs with the parameter form's values, follows the run live, and stops it", async () => {
  vi.mocked(workflowsApi.start).mockResolvedValue(summary({ status: "running", finished_at: null }));
  const running = detail({
    status: "running", finished_at: null, delivery_status: "pending", delivery_message: null, deliveries: [],
    steps: [step("extract", "sql", "running"), step("check", "qc_builtin", "pending")],
  }); // prettier-ignore
  vi.mocked(workflowsApi.run).mockResolvedValue(running);
  vi.mocked(workflowsApi.stop).mockResolvedValue(summary({ status: "cancelled" }));
  show(FILE);
  fireEvent.change(await screen.findByRole("spinbutton"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  await waitFor(() =>
    expect(workflowsApi.start).toHaveBeenCalledWith("weekly_steps.yaml", { start_date: "2025-04-01", min_cell: 20, suppress: false }),
  );
  const steps = await screen.findByRole("list", { name: "Steps" });
  expect(within(steps).getByText("running")).toBeInTheDocument();
  await waitFor(() => expect(FakeEventSource.last?.url).toBe("/stream/run_1"));

  // A snapshot over the stream: the extract is done, the check is running.
  act(() =>
    FakeEventSource.last!.emit("run", {
      ...running,
      steps: [step("extract", "sql", "succeeded", { elapsed_ms: 1500 }), step("check", "qc_builtin", "running")],
    }),
  );
  expect(await within(steps).findByText("done")).toBeInTheDocument();
  expect(within(steps).getByText("1.5 s")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
  await waitFor(() => expect(workflowsApi.stop).toHaveBeenCalledWith("run_1"));
  act(() =>
    FakeEventSource.last!.emit("run", {
      ...running, status: "cancelled", finished_at: "2026-09-27T12:02:00Z", message: "Stopped.",
      delivery_status: "skipped", delivery_message: "The run was stopped.",
      steps: [step("extract", "sql", "succeeded"), step("check", "qc_builtin", "cancelled")],
    }), // prettier-ignore
  );
  expect(await screen.findByText("Stopped.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument();
});

it("won't run with a number the browser couldn't read", async () => {
  show(FILE);
  const field = await screen.findByRole("spinbutton");
  // What a browser reports for "1e": an empty value, and badInput.
  Object.defineProperty(field, "validity", { value: { badInput: true, stepMismatch: false } });
  fireEvent.change(field, { target: { value: "" } });
  expect(screen.getByText("This isn't a number.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(await screen.findByText(/Nothing was run/)).toBeInTheDocument();
  expect(workflowsApi.start).not.toHaveBeenCalled();
});

it("asks for the run instead once its live updates close for good", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({ status: "running", finished_at: null, delivery_status: "pending", deliveries: [] }),
  );
  show("/workflows/runs/run_1");
  await waitFor(() => expect(FakeEventSource.last).not.toBeNull());
  const calls = vi.mocked(workflowsApi.run).mock.calls.length;
  vi.mocked(workflowsApi.run).mockResolvedValue(detail());
  act(() => {
    FakeEventSource.last!.readyState = FakeEventSource.CLOSED;
    FakeEventSource.last!.emit("error", {});
  });
  await waitFor(() => expect(vi.mocked(workflowsApi.run).mock.calls.length).toBeGreaterThan(calls));
  expect(await screen.findByText("succeeded")).toBeInTheDocument();
});

it("puts the start's parameter problems beside their fields", async () => {
  vi.mocked(workflowsApi.start).mockRejectedValue(
    new ApiError(422, "The workflow doesn't pass its checks.", {
      message: "The workflow doesn't pass its checks.",
      problems: [{ path: "params.start_date", message: "Give a date as YYYY-MM-DD." }],
    }),
  );
  show(FILE);
  fireEvent.click(await screen.findByRole("button", { name: "Run" }));
  expect(await screen.findByText("Give a date as YYYY-MM-DD.")).toBeInTheDocument();
});

it("refuses Stop, with the reason, once delivery has started", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({ status: "succeeded", finished_at: null, delivery_status: "pending", delivery_message: null, deliveries: [] }),
  );
  show("/workflows/runs/run_1");
  expect(await screen.findByText(/can't be stopped once its delivery has started/)).toBeInTheDocument();
  expect(screen.getByText("Delivering")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument();
});

it("shows the server's refusal if Stop comes as delivery starts", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({
      status: "running", finished_at: null, delivery_status: "pending", deliveries: [],
      steps: [step("extract", "sql", "succeeded"), step("check", "qc_builtin", "running")],
    }), // prettier-ignore
  );
  vi.mocked(workflowsApi.stop).mockRejectedValue(
    new ApiError(409, "Delivery has started, so this run can't be stopped now."),
  );
  show("/workflows/runs/run_1");
  fireEvent.click(await screen.findByRole("button", { name: /Stop/ }));
  expect(await screen.findByText("Delivery has started, so this run can't be stopped now.")).toBeInTheDocument();
});

it("shows a failed check with its counts, and that nothing was delivered", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({
      status: "failed", message: "Step check failed, so the rest didn't run.",
      delivery_status: "skipped", delivery_message: "A step failed, so nothing was delivered.", deliveries: [],
      steps: [
        step("extract", "sql", "succeeded"),
        step("check", "qc_builtin", "failed", {
          message: "Failed: small_cells (3)",
          result: { status: "failed", checks: [
            { id: "min_rows", status: "pass", observed: 120, expected: ">= 1", message: "120 rows" },
            { id: "small_cells", status: "fail", observed: 3, expected: 0, message: "3 counts from 1 to 10 are shown" },
          ] },
        }),
        step("summary", "r", "skipped"),
      ],
    }), // prettier-ignore
  );
  show("/workflows/runs/run_1");
  expect(await screen.findByText("Step check failed, so the rest didn't run.")).toBeInTheDocument();
  expect(screen.getByText("1 of 2 checks failed")).toBeInTheDocument();
  // A failed step opens by itself onto its checks.
  expect(screen.getByText("3 counts from 1 to 10 are shown")).toBeInTheDocument();
  expect(screen.getByText("didn't run")).toBeInTheDocument();
  expect(screen.getByText(/Nothing was delivered/)).toBeInTheDocument();
  expect(screen.getByText("A step failed, so nothing was delivered.")).toBeInTheDocument();
});

it("shows what a run pinned and where it delivered, and runs it again", async () => {
  vi.mocked(workflowsApi.again).mockResolvedValue(summary({ id: "run_2", mode: "run_again", status: "running" }));
  show("/workflows/runs/run_1");
  const pinned = await screen.findByText("What this run pinned");
  const section = pinned.closest("section")!;
  expect(within(section).getByText("42")).toBeInTheDocument();
  expect(within(section).getByText(/sha256:1234567890abcdef · linux\/arm64/)).toBeInTheDocument();
  expect(within(section).getByText("sha256 aaaabbbbcccc")).toBeInTheDocument();
  expect(within(section).getByText(/not committed · sha256:abc/)).toBeInTheDocument();
  expect(screen.getByText(/^Saved to Lab Dropbox \(on this computer\): 1 file\./)).toBeInTheDocument();
  // Each delivery: saved on this computer, and who uploads it. Never "synced".
  expect(screen.getByText("Saved to Lab Dropbox (on this computer)")).toBeInTheDocument();
  expect(
    screen.getByText("Dropbox will upload it when its app is running and signed in. DataLab can't confirm the upload."),
  ).toBeInTheDocument();
  expect(document.body.textContent?.toLowerCase()).not.toMatch(/synced|uploaded/);
  expect(screen.getByText("weekly.csv")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Run again/ }));
  await waitFor(() => expect(workflowsApi.again).toHaveBeenCalledWith("run_1"));
  await waitFor(() => expect(workflowsApi.run).toHaveBeenCalledWith("run_2"));
});

it("says Run again delivers where the file says now", async () => {
  show("/workflows/runs/run_1");
  const note = await screen.findByText(/on today's data: new results/);
  await waitFor(() => expect(note.textContent).toContain("delivered to practice-folder if every check passes"));
});

it("shows the replay check's reasons and asks before an inexact replay, and again before it delivers", async () => {
  vi.mocked(workflowsApi.replayCheck).mockResolvedValue({
    exact: false,
    reasons: ["DataLab's step wrapper has changed since this run."],
    blocking: [],
  });
  vi.mocked(workflowsApi.replay).mockResolvedValue(summary({ id: "run_3", mode: "replay", status: "running" }));
  show("/workflows/runs/run_1");
  fireEvent.click(await screen.findByRole("button", { name: /^Replay/ }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("This Replay can't be exact:")).toBeInTheDocument();
  expect(within(dialog).getByText("DataLab's step wrapper has changed since this run.")).toBeInTheDocument();
  const go = within(dialog).getByRole("button", { name: "Replay, not exact" });
  expect(go).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("checkbox", { name: /Replay anyway/ }));
  expect(go).toBeEnabled();

  fireEvent.click(within(dialog).getByRole("checkbox", { name: /Deliver the replay's outputs too/ }));
  const ask = within(dialog).getByRole("button", { name: "Replay and deliver…" });
  // A double-click (or a held Enter) on the first button can't answer the second question.
  fireEvent.click(ask);
  fireEvent.click(ask);
  expect(within(dialog).getByText("Deliver this replay?")).toBeInTheDocument();
  expect(ask).toBeDisabled();
  const yes = within(dialog).getByRole("button", { name: "Yes, deliver" });
  expect(yes).toBeDisabled(); // for a moment after it appears
  fireEvent.click(yes);
  expect(workflowsApi.replay).not.toHaveBeenCalled();
  await waitFor(() => expect(yes).toBeEnabled(), { timeout: 2000 });
  fireEvent.click(yes);
  await waitFor(() => expect(workflowsApi.replay).toHaveBeenCalledWith("run_1", { allow_inexact: true, deliver: true }));
  expect(workflowsApi.replay).toHaveBeenCalledTimes(1);
});

it("never shows a custom check's text as its found or wanted value", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({
      status: "failed", delivery_status: "skipped", deliveries: [],
      steps: [
        step("custom", "qc_custom", "failed", {
          result: {
            status: "failed", counts: { rows: 12, who: "SYN-CANARY-2" },
            checks: [{ id: "who", status: "fail", observed: "SYN-CANARY-1", expected: "SYN-CANARY-0", message: "" }],
          },
        }),
      ],
    }), // prettier-ignore
  );
  show("/workflows/runs/run_1");
  expect(await screen.findByText("who")).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("SYN-CANARY");
});

it("replays an exact run without delivering unless asked", async () => {
  vi.mocked(workflowsApi.replayCheck).mockResolvedValue({ exact: true, reasons: [], blocking: [] });
  vi.mocked(workflowsApi.replay).mockResolvedValue(summary({ id: "run_3", mode: "replay", status: "running" }));
  show("/workflows/runs/run_1");
  fireEvent.click(await screen.findByRole("button", { name: /^Replay/ }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText(/This Replay can be exact/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Replay" }));
  await waitFor(() => expect(workflowsApi.replay).toHaveBeenCalledWith("run_1", { allow_inexact: false, deliver: false }));
});

it("says why a run can't be replayed at all", async () => {
  vi.mocked(workflowsApi.replayCheck).mockResolvedValue({
    exact: false,
    reasons: [],
    blocking: ["This run's extracted inputs have been removed."],
  });
  show("/workflows/runs/run_1");
  fireEvent.click(await screen.findByRole("button", { name: /^Replay/ }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("This run's extracted inputs have been removed.")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Replay" })).toBeDisabled();
});

it("says whether a finished replay matched the original byte for byte", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({ id: "run_3", mode: "replay", of_run: "run_1", reproduced: true, replay_exact: true, delivery_status: "skipped",
      delivery_message: "Replays don't deliver unless asked to.", deliveries: [] }), // prettier-ignore
  );
  show("/workflows/runs/run_3");
  expect(await screen.findByText(/Every output matched the original byte for byte/)).toBeInTheDocument();
  expect(screen.getByText("Replays don't deliver unless asked to.")).toBeInTheDocument();
});

it("lists what didn't match in a replay that differed", async () => {
  vi.mocked(workflowsApi.run).mockResolvedValue(
    detail({ id: "run_3", mode: "replay", of_run: "run_1", reproduced: false, replay_exact: false,
      replay_notes: ["DataLab's step wrapper has changed since this run.", "Step summary's weekly.csv differs from the original."] }), // prettier-ignore
  );
  show("/workflows/runs/run_3");
  expect(await screen.findByText(/Not everything matched the original/)).toBeInTheDocument();
  expect(screen.getByText("Step summary's weekly.csv differs from the original.")).toBeInTheDocument();
  expect(screen.getByText(/couldn't be exact/)).toBeInTheDocument();
});

it("docks a chat that is offered the workflow file, not sent it", async () => {
  sessionStorage.setItem("datalab:workflows:chat-open", "open");
  show(FILE);
  await waitFor(() =>
    expect(chatProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        mode: "workflows",
        context: { label: "The workflow file weekly_steps.yaml", text: YAML, language: "yaml" },
      }),
    ),
  );
});

it("points to the chat's change waiting for review in Pipelines", async () => {
  sessionStorage.setItem("datalab:workflows:chat-open", "open");
  sessionStorage.setItem("datalab:workflows:chat", "c_mine");
  vi.mocked(api.conversations).mockResolvedValue([{ id: "c_mine" }] as never);
  const proposal = (id: string, conversation_id: string, status: string) => ({ id, conversation_id, status });
  vi.mocked(pipelinesApi.proposals).mockResolvedValue([
    proposal("p_saved", "c_mine", "saved"),
    proposal("p_other", "c_other", "open"),
    proposal("p_mine", "c_mine", "open"),
  ] as never);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workflows"]}>
        <Routes>
          <Route path="workflows/*" element={<WorkflowsPage />} />
          <Route path="pipelines" element={<p>Pipelines tab</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("This chat's change is waiting for review in Pipelines.")).toBeInTheDocument();
  expect(pipelinesApi.proposals).toHaveBeenCalledWith("c_mine");
  fireEvent.click(screen.getByRole("button", { name: "Review it" }));
  expect(await screen.findByText("Pipelines tab")).toBeInTheDocument();
  expect(sessionStorage.getItem("datalab:pipelines:proposal")).toBe("p_mine");
});

it("shows a loading state while the list loads, never the empty call to action, then the list", async () => {
  let resolve!: (value: Workflow[]) => void;
  vi.mocked(workflowsApi.list).mockReturnValue(new Promise((r) => (resolve = r)));
  show("/workflows");
  const loading = await screen.findByRole("status");
  expect(loading).toHaveTextContent("Loading workflows…");
  expect(loading).toHaveAttribute("aria-live", "polite");
  expect(screen.queryByText("Make your first workflow")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /New workflow/ })).not.toBeInTheDocument();
  await act(async () => resolve([workflow()]));
  expect(await screen.findByRole("list", { name: "Workflows" })).toBeInTheDocument();
  expect(screen.queryByText("Loading workflows…")).not.toBeInTheDocument();
});

it("shows the empty state only once the list has loaded and is empty", async () => {
  vi.mocked(workflowsApi.list).mockResolvedValue([]);
  show("/workflows");
  expect(await screen.findByText("Make your first workflow")).toBeInTheDocument();
  expect(screen.queryByText("Loading workflows…")).not.toBeInTheDocument();
});

it("says when the list couldn't be read, and Retry reads it again", async () => {
  vi.mocked(workflowsApi.list).mockRejectedValueOnce(new Error("DataLab couldn't be reached.")).mockResolvedValue([workflow()]);
  show("/workflows");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("DataLab couldn't be reached.");
  expect(screen.queryByText("Make your first workflow")).not.toBeInTheDocument();
  fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("list", { name: "Workflows" })).toBeInTheDocument();
  expect(workflowsApi.list).toHaveBeenCalledTimes(2);
});

it("puts New workflow and Ask for help in one header action area, New workflow last, with no padding kept for them", async () => {
  sessionStorage.setItem("datalab:workflows:chat-open", "closed");
  show("/workflows");
  const link = await screen.findByRole("link", { name: /New workflow/ });
  const area = link.parentElement!;
  expect(area).toHaveAttribute("data-header-actions");
  expect(area.className).toMatch(/\bml-auto\b/);
  expect(area.className).toMatch(/\bflex-wrap\b/);
  expect(area.className).toMatch(/\bjustify-end\b/);
  const ask = within(area).getByRole("button", { name: /Ask for help/ });
  expect(area.lastElementChild).toBe(link);
  expect(area.firstElementChild).toBe(ask);
  const header = area.closest("header")!;
  expect(header).toHaveAttribute("data-page-header");
  expect(header.className).toMatch(/\bflex-wrap\b/);
  expect(header.className).not.toMatch(/\bpr-\d/);
  // The header spans the same column as the list below it: no padding of its own on the right.
  expect(header.parentElement).toBe(screen.getByRole("list", { name: "Workflows" }).parentElement);
  // No second, floating Ask for help over the page.
  expect(screen.getAllByRole("button", { name: /Ask for help/ })).toHaveLength(1);

  // With the chat open, New workflow stays at the end of the same area.
  fireEvent.click(ask);
  expect(screen.queryByRole("button", { name: /Ask for help/ })).not.toBeInTheDocument();
  expect(link.parentElement).toBe(area);
  expect(area.lastElementChild).toBe(link);
});

it("gives New workflow's page the same header, with Ask for help at its right", async () => {
  sessionStorage.setItem("datalab:workflows:chat-open", "closed");
  show("/workflows/new");
  const heading = await screen.findByRole("heading", { level: 1, name: "New workflow" });
  const header = heading.closest("header")!;
  expect(header).toHaveAttribute("data-page-header");
  expect(header.className).not.toMatch(/\bpr-\d/);
  const area = header.querySelector("[data-header-actions]")!;
  expect(within(area as HTMLElement).getByRole("button", { name: /Ask for help/ })).toBeInTheDocument();
});

it("on a narrow window, the files drawer takes focus, keeps it, closes on Escape and gives focus back", async () => {
  show("/workflows");
  const menu = await screen.findByRole("button", { name: "Show workflow files" });
  menu.focus();
  fireEvent.click(menu);
  const drawer = screen.getByRole("dialog", { name: "Workflow files" });
  const links = within(drawer).getAllByRole("link");
  await waitFor(() => expect(links[0]).toHaveFocus());
  links.at(-1)!.focus();
  fireEvent.keyDown(links.at(-1)!, { key: "Tab" });
  expect(links[0]).toHaveFocus();
  fireEvent.keyDown(links[0], { key: "Tab", shiftKey: true });
  expect(links.at(-1)).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  await waitFor(() => expect(menu).toHaveFocus());
  expect(screen.queryByRole("dialog", { name: "Workflow files" })).toBeNull();
});

it("on a narrow window, the chat is a dialog; closed with Escape it's kept but inactive, and focus returns to Ask for help", async () => {
  sessionStorage.setItem("datalab:workflows:chat-open", "closed");
  show("/workflows");
  const ask = await screen.findByRole("button", { name: /Ask for help/ });
  ask.focus();
  fireEvent.click(ask);
  const box = screen.getByRole("dialog", { name: "Workflow authoring chat" });
  await waitFor(() => expect(box).toContainElement(document.activeElement as HTMLElement));
  expect((chatProps.mock.lastCall![0] as { active: boolean }).active).toBe(true);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  await waitFor(() => expect(screen.getByRole("button", { name: /Ask for help/ })).toHaveFocus());
  expect(box).not.toBeVisible();
  expect((chatProps.mock.lastCall![0] as { active: boolean }).active).toBe(false);
});
