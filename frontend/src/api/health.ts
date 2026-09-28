import { request } from "./http";

export interface Health {
  status: string;
  version: string;
  profile: "real" | "practice";
  database_configured: boolean;
  catalog_tables: number;
  /** Why the catalog is empty and what happens next; null once it has tables. */
  catalog_problem?: string | null;
}

export const healthApi = {
  health: () => request<Health>("/api/health"),
};
