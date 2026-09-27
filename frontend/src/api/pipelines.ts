// Calls for the Pipelines tab (backend api/pipelines.py). Add this tab's calls
// here: it's the only API file its work needs to touch.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type PipelinesStatus = Schemas["PipelinesStatus"];
export type PipelineTree = Schemas["PipelineTreeOut"];
export type PipelineFile = Schemas["PipelineFileOut"];
export type PipelineProposal = Schemas["PipelineProposalOut"];
export type PipelineProposalDetail = Schemas["PipelineProposalDetail"];
export type PipelineFileDiff = Schemas["PipelineFileDiff"];
export type PipelineFinding = Schemas["PipelineFindingOut"];
export type PipelineTest = Schemas["PipelineTestOut"];
export type PipelineTestLog = Schemas["PipelineTestLogOut"];

const path = (p: string) => p.split("/").map(encodeURIComponent).join("/");

export const pipelinesApi = {
  /** The pipelines repo's clone: configured, signed in, synced. */
  status: () => request<PipelinesStatus>("/api/pipelines/status"),
  sync: () => request<PipelinesStatus>("/api/pipelines/sync", { method: "POST" }),
  /** The repo's files, at GitHub's main as last synced. */
  files: () => request<PipelineTree>("/api/pipelines/files"),
  file: (p: string) => request<PipelineFile>(`/api/pipelines/files/${path(p)}`),
  proposals: (conversationId?: string) =>
    request<PipelineProposal[]>(
      "/api/pipelines/proposals" + (conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ""),
    ),
  proposal: (id: string) => request<PipelineProposalDetail>(`/api/pipelines/proposals/${id}`),
  /** Run the package's tests on a proposal, in the background. */
  test: (id: string) => request<PipelineProposalDetail>(`/api/pipelines/proposals/${id}/tests`, { method: "POST" }),
  /** Save & share, in the background: it runs the tests first if they haven't passed. */
  accept: (id: string, confirmed: string[]) =>
    request<PipelineProposalDetail>(`/api/pipelines/proposals/${id}/accept`, {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    }),
  reject: (id: string) => request<PipelineProposalDetail>(`/api/pipelines/proposals/${id}/reject`, { method: "POST" }),
  testLog: (id: string) => request<PipelineTestLog>(`/api/pipelines/tests/${id}/log`),
};
