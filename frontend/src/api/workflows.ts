// Calls for the Workflows tab. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { type FeatureStatus, request } from "./http";

export const workflowsApi = {
  /** Whether the Workflows tab's backend is ready. */
  status: () => request<FeatureStatus>("/api/workflows/status"),
};
