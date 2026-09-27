// Calls for the Knowledge tab. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { type FeatureStatus, request } from "./http";

export const knowledgeApi = {
  /** Whether the Knowledge tab's backend is ready. */
  status: () => request<FeatureStatus>("/api/knowledge/status"),
};
