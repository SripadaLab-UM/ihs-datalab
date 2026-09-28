import { request } from "./http";
import type { components } from "./schema";
import type { PracticeDatabase } from "./settings";

/** Why the catalog is empty and what happens next (signed in only). */
export type CatalogStatus = components["schemas"]["CatalogStatusOut"];

export interface Health {
  status: string;
  version: string;
  profile: "real" | "practice";
  database_configured: boolean;
  catalog_tables: number;
  /** Coarse on purpose: health needs no sign-in. The details are in catalogStatus. */
  catalog_state?: CatalogStatus["state"];
  /** Practice only: how its synthetic database stands (details in Settings → Connections). */
  practice_database?: PracticeDatabase["phase"] | null;
}

export const healthApi = {
  health: () => request<Health>("/api/health"),
  catalogStatus: () => request<CatalogStatus>("/api/catalog/status"),
};
