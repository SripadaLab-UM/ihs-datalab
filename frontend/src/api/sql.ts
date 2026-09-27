// Calls for the SQL Playground. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { type FeatureStatus, request } from "./http";

export const sqlApi = {
  /** Whether the SQL Playground's backend is ready. */
  status: () => request<FeatureStatus>("/api/sql/status"),
};
