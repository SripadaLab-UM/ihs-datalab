// Settings: Connections, Storage, Updates, Copy diagnostics, and the workflow
// destination keys. Secrets only ever go one way: saved, never read back.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Connections = Schemas["ConnectionsOut"];
export type ConnectionTest = Schemas["ConnectionTestOut"];
export type Storage = Schemas["StorageOut"];
export type StorageGroup = Schemas["StorageGroupOut"];
export type StorageItem = Schemas["StorageItemOut"];
export type StorageRemoved = Schemas["StorageRemovedOut"];
export type RemovableKind = Schemas["StorageRemoveIn"]["kind"];
export type Updates = Schemas["UpdatesOut"];
export type BackupInfo = Schemas["BackupInfoOut"];
export type DestinationKey = Schemas["DestinationKeyOut"];

export const settingsApi = {
  connections: () => request<Connections>("/api/settings/connections"),
  saveDatabasePassword: (password: string) =>
    request<void>("/api/settings/connections/database-password", {
      method: "PUT",
      body: JSON.stringify({ password }),
    }),
  saveModelKey: (key: string) =>
    request<void>("/api/settings/connections/model-key", { method: "PUT", body: JSON.stringify({ key }) }),
  testConnections: () => request<ConnectionTest>("/api/settings/connections/test", { method: "POST" }),

  storage: () => request<Storage>("/api/settings/storage"),
  removeStorageItem: (kind: RemovableKind, id: string, confirmed = false) =>
    request<StorageRemoved>("/api/settings/storage/remove", {
      method: "POST",
      body: JSON.stringify({ kind, id, confirmed }),
    }),

  updates: () => request<Updates>("/api/settings/updates"),
  diagnostics: () => request<{ text: string }>("/api/settings/diagnostics"),

  /** The destination keys workflow files name, and the export folder each maps to here. */
  destinationKeys: () => request<DestinationKey[]>("/api/workflows/destinations"),
  setDestinationKey: (key: string, destinationId: string) =>
    request<void>(`/api/workflows/destinations/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ destination_id: destinationId }),
    }),
};

/** A size in bytes, the way Finder and Explorer say it. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value).toLocaleString()} ${units[unit]}`;
}
