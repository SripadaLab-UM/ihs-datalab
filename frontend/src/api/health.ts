import { request } from "./http";

export interface Health {
  status: string;
  version: string;
  profile: "real" | "practice";
  database_configured: boolean;
  catalog_tables: number;
}

export const healthApi = {
  health: () => request<Health>("/api/health"),
};
