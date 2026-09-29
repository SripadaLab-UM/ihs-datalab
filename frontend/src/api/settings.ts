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
export type UpdateCheck = Schemas["UpdateCheckOut"];
export type UpdateRelease = Schemas["ReleaseOut"];
export type UpdateInstall = Schemas["UpdateInstallOut"];
export type BackupInfo = Schemas["BackupInfoOut"];
export type DestinationKey = Schemas["DestinationKeyOut"];
export type FeedbackContact = Schemas["FeedbackContactOut"];
export type PracticeDatabase = Schemas["PracticeDatabaseOut"];

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
  /** The last Test connection's result since DataLab started (from any window, or the API), or null. */
  lastConnectionTest: () => request<ConnectionTest | null>("/api/settings/connections/test"),
  /** Practice only: its synthetic database, which DataLab sets up and starts itself. */
  practiceDatabase: () => request<PracticeDatabase>("/api/settings/practice-database"),
  startPracticeDatabase: () => request<PracticeDatabase>("/api/settings/practice-database/start", { method: "POST" }),
  /** Delete it and set it up again from scratch. Only after the person confirmed. */
  resetPracticeDatabase: () =>
    request<PracticeDatabase>("/api/settings/practice-database/reset", {
      method: "POST",
      body: JSON.stringify({ confirmed: true }),
    }),

  storage: () => request<Storage>("/api/settings/storage"),
  removeStorageItem: (kind: RemovableKind, id: string, confirmed = false) =>
    request<StorageRemoved>("/api/settings/storage/remove", {
      method: "POST",
      body: JSON.stringify({ kind, id, confirmed }),
    }),

  updates: () => request<Updates>("/api/settings/updates"),
  /** What the last check for a newer release found. No network: DataLab asks GitHub at start and hourly. */
  updateCheck: () => request<UpdateCheck>("/api/settings/updates/check"),
  /** Ask GitHub now (DataLab's host does; at most once a minute). */
  checkForUpdates: () => request<UpdateCheck>("/api/settings/updates/check", { method: "POST" }),
  /** Check about once an hour while DataLab is open, or not (this computer). Asks GitHub nothing. */
  setCheckEveryHour: (on: boolean) =>
    request<UpdateCheck>("/api/settings/updates/every-hour", { method: "PUT", body: JSON.stringify({ on }) }),
  /** Install the release on offer. Only after the person confirmed; DataLab restarts at the end. */
  installUpdate: (version: string) =>
    request<UpdateCheck>("/api/settings/updates/install", {
      method: "POST",
      body: JSON.stringify({ version, confirmed: true }),
    }),
  diagnostics: () => request<{ text: string }>("/api/settings/diagnostics"),
  /** Whom the toolbar's Feedback goes to: the lab's repos.access_contact, and the email address in it. */
  feedbackContact: () => request<FeedbackContact>("/api/settings/feedback-contact"),

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
