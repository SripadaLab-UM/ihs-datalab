// The Safety check on the Settings page.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type SafetyReport = Schemas["SafetyReportOut"];
export type CheckResult = Schemas["CheckResultOut"];

export const safetyApi = {
  lastSafetyReport: () => request<SafetyReport | null>("/api/safety/last"),
  runSafetyCheck: () => request<SafetyReport>("/api/safety/check", { method: "POST" }),
};
