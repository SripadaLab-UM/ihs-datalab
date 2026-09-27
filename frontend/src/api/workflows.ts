// Calls for the Workflows tab. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Workflow = Schemas["WorkflowOut"];
export type WorkflowText = Schemas["WorkflowTextOut"];
export type WorkflowProblem = Schemas["ProblemOut"];
export type WorkflowParameter = Schemas["ParameterOut"];
export type WorkflowStepSummary = Schemas["StepSummary"];
export type WorkflowRun = Schemas["WorkflowRunOut"];
export type RunDetail = Schemas["RunDetailOut"];
export type RunStep = Schemas["StepOut"];
export type Delivery = Schemas["DeliveryOut"];
export type ReplayCheck = Schemas["ReplayCheckOut"];
export type DestinationKey = Schemas["DestinationKeyOut"];
export type ParamValue = boolean | number | string;

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});
const id = encodeURIComponent;

export const workflowsApi = {
  /** Whether the Workflows tab's backend is ready. */
  status: () => request<Schemas["WorkflowsStatus"]>("/api/workflows/status"),
  /** Every workflow file in the folder, checked, with its newest run. */
  list: () => request<Workflow[]>("/api/workflows"),
  /** A workflow file's text as it is in the folder now. */
  text: (path: string) => request<WorkflowText>(`/api/workflows/text?path=${id(path)}`),
  /** Runs, newest first: of one file, or of every file. */
  runs: (path?: string) => request<WorkflowRun[]>(`/api/workflows/runs${path ? `?path=${id(path)}` : ""}`),
  run: (runId: string) => request<RunDetail>(`/api/workflows/runs/${id(runId)}`),
  /** Start a run. A 422's detail has `problems` (`params.<name>`); a 409 says why it was refused. */
  start: (path: string, params: Record<string, ParamValue>) =>
    request<WorkflowRun>("/api/workflows/runs", post({ path, params })),
  /** Refused (409) once delivery has started. */
  stop: (runId: string) => request<WorkflowRun>(`/api/workflows/runs/${id(runId)}/stop`, post()),
  /** The current file afresh: today's data, the same parameters and seed. */
  again: (runId: string) => request<WorkflowRun>(`/api/workflows/runs/${id(runId)}/again`, post()),
  /** Whether a Replay would be exact and, if not, why; and what stops it altogether. */
  replayCheck: (runId: string) => request<ReplayCheck>(`/api/workflows/runs/${id(runId)}/replay`),
  replay: (runId: string, options: { allow_inexact: boolean; deliver: boolean }) =>
    request<WorkflowRun>(`/api/workflows/runs/${id(runId)}/replay`, post(options)),
  /** The destination keys workflow files name, and the folder each maps to on this computer. */
  destinations: () => request<DestinationKey[]>("/api/workflows/destinations"),
};

/** Where a run's live updates come from (server-sent events: `run` each time it changes, then `end`). */
export const runStreamUrl = (runId: string) => `/api/workflows/runs/${id(runId)}/stream`;
