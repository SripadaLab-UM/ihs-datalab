// Calls for the Pipelines tab. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { type FeatureStatus, request } from "./http";

export const pipelinesApi = {
  /** Whether the Pipelines tab's backend is ready. */
  status: () => request<FeatureStatus>("/api/pipelines/status"),
};
