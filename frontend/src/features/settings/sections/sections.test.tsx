import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import type { Connections, Storage, StorageItem, Updates } from "@/api/settings";
import { formatBytes, settingsApi } from "@/api/settings";

import { ConnectionsSection } from "./ConnectionsSection";
import { DestinationKeysSection } from "./DestinationKeysSection";
import { DiagnosticsSection } from "./DiagnosticsSection";
import { StorageSection } from "./StorageSection";
import { UpdatesSection } from "./UpdatesSection";

vi.mock("@/api/client", () => ({
  api: {
    destinations: vi.fn(async () => [
      { id: "dest_1", name: "Lab Dropbox", path: "/Users/me/Dropbox/Lab", available: true },
    ]),
  },
}));

vi.mock("@/api/settings", async (original) => ({
  ...(await original<typeof import("@/api/settings")>()),
  settingsApi: {
    connections: vi.fn(),
    saveDatabasePassword: vi.fn(async () => undefined),
    saveModelKey: vi.fn(async () => undefined),
    testConnections: vi.fn(),
    storage: vi.fn(),
    removeStorageItem: vi.fn(),
    updates: vi.fn(),
    diagnostics: vi.fn(),
    destinationKeys: vi.fn(),
    setDestinationKey: vi.fn(async () => undefined),
  },
}));

const mocked = vi.mocked(settingsApi);

function wrap(children: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>);
}

const REAL: Connections = {
  profile: "real",
  settings_file: "/Users/me/Library/Application Support/DataLab/real/settings.toml",
  oracle: {
    configured: true,
    practice: false,
    dsn: "db.example:1521/SVC",
    user: "SVC_READER",
    read_only_roles: ["IHS_2025_RO"],
    allowed_schemas: ["IHS_2025"],
    password: "missing",
    can_set_password: true,
  },
  model: { base_url: "https://api.example/v1", key: "keychain", can_set_key: true },
  read_only_because: null,
};

beforeEach(() => vi.clearAllMocks());

it("saves a password write-only and clears the field", async () => {
  mocked.connections.mockResolvedValue(REAL);
  wrap(<ConnectionsSection />);
  expect(await screen.findByText("Not saved yet")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save the password…" }));
  const field = screen.getByLabelText("Database password for SVC_READER") as HTMLInputElement;
  expect(field.type).toBe("password");
  fireEvent.change(field, { target: { value: "s3cret" } });
  fireEvent.click(screen.getByRole("button", { name: "Save to keychain" }));
  await waitFor(() => expect(mocked.saveDatabasePassword).toHaveBeenCalledWith("s3cret", expect.anything()));
  expect(await screen.findByText("Saved")).toBeTruthy();
  expect(screen.queryByLabelText("Database password for SVC_READER")).toBeNull();
  expect(document.body.textContent).not.toContain("s3cret");
  expect(screen.getByRole("button", { name: "Replace the key…" })).toBeTruthy();
});

it("tests the connection and says how it went", async () => {
  mocked.connections.mockResolvedValue(REAL);
  mocked.testConnections.mockResolvedValue({
    database: { ok: false, message: "Can't reach the database — are you on the VPN? (DPY-6005)", enabled_roles: [], read_only: null },
    model: { ok: true, message: "U-M GPT accepted the key and offers 3 approved models." },
  });
  wrap(<ConnectionsSection />);
  await screen.findByText("Not saved yet");
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByText(/are you on the VPN/)).toBeTruthy();
  expect(screen.getByText(/offers 3 approved models/)).toBeTruthy();
});

it("practice shows its synthetic database and offers no changes", async () => {
  mocked.connections.mockResolvedValue({
    ...REAL,
    profile: "practice",
    oracle: { ...REAL.oracle, practice: true, password: null, can_set_password: false },
    model: { ...REAL.model, can_set_key: false },
    read_only_because: "Practice DataLab uses the local synthetic database, whose settings are fixed.",
  });
  wrap(<ConnectionsSection />);
  expect(await screen.findByText(/whose settings are fixed/)).toBeTruthy();
  expect(screen.getByText("Fixed: the synthetic database's own")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Save the|Replace the/ })).toBeNull();
});

function item(overrides: Partial<StorageItem>): StorageItem {
  return {
    kind: "backup",
    id: "x",
    label: "x",
    detail: "",
    size_bytes: 2048,
    modified_at: null,
    removable: true,
    not_removable_because: null,
    kept: false,
    kept_because: null,
    removing_loses: "What goes.",
    needs_confirmation: false,
    ...overrides,
  };
}

const STORAGE: Storage = {
  data_dir: "/Users/me/Library/Application Support/DataLab/real",
  size_bytes: 5 * 1024 ** 2,
  free_bytes: 100 * 1024 ** 3,
  groups: [
    {
      id: "database",
      title: "DataLab's database",
      about: "Never removed.",
      size_bytes: 4096,
      items: [item({ kind: "database", id: "datalab.sqlite", label: "datalab.sqlite", removable: false, not_removable_because: "Never." })],
    },
    {
      id: "backups",
      title: "Database backups",
      about: "Copies.",
      size_bytes: 4096,
      items: [
        item({ id: "0.1.0-a", label: "0.1.0-a", removing_loses: "This backup of DataLab's database." }),
        item({
          id: "0.1.0-r",
          label: "0.1.0-r",
          kept: true,
          kept_because: "Taken before a rollback replaced the database.",
          needs_confirmation: true,
          removing_loses: "It may be the only copy.",
        }),
      ],
    },
  ],
};

it("lists storage and removes an item only after confirming", async () => {
  mocked.storage.mockResolvedValue(STORAGE);
  mocked.removeStorageItem.mockResolvedValue({ kind: "backup", id: "0.1.0-a", freed_bytes: 2048 });
  wrap(<StorageSection />);
  expect(await screen.findByText(/DataLab's data folder uses/)).toBeTruthy();
  expect(screen.getByText("5.0 MB")).toBeTruthy();
  // The database is never offered.
  const database = screen.getByText("datalab.sqlite").closest("li") as HTMLElement;
  expect(within(database).queryByRole("button")).toBeNull();

  const plain = screen.getByText("0.1.0-a").closest("li") as HTMLElement;
  fireEvent.click(within(plain).getByRole("button", { name: "Remove…" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("This backup of DataLab's database.")).toBeTruthy();
  expect(mocked.removeStorageItem).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: /Remove$/ }));
  await waitFor(() => expect(mocked.removeStorageItem).toHaveBeenCalledWith("backup", "0.1.0-a", false));
  expect(await screen.findByText("Removed. That freed 2.0 KB.")).toBeTruthy();
});

it("a rollback's backup needs the person to say they understand", async () => {
  mocked.storage.mockResolvedValue(STORAGE);
  mocked.removeStorageItem.mockResolvedValue({ kind: "backup", id: "0.1.0-r", freed_bytes: 2048 });
  wrap(<StorageSection />);
  const kept = (await screen.findByText("0.1.0-r")).closest("li") as HTMLElement;
  expect(within(kept).getByText("kept for good")).toBeTruthy();
  fireEvent.click(within(kept).getByRole("button", { name: "Remove…" }));
  const dialog = await screen.findByRole("dialog");
  const remove = within(dialog).getByRole("button", { name: /Remove$/ }) as HTMLButtonElement;
  expect(remove.disabled).toBe(true);
  fireEvent.click(within(dialog).getByRole("checkbox"));
  expect(remove.disabled).toBe(false);
  fireEvent.click(remove);
  await waitFor(() => expect(mocked.removeStorageItem).toHaveBeenCalledWith("backup", "0.1.0-r", true));
});

it("updates show the version, what needs you, and the backups", async () => {
  const updates: Updates = {
    version: "0.1.0a2",
    check_available: false,
    check_message: "Update checks aren't set up yet.",
    marker: null,
    marker_unreadable: false,
    recovery: { outcome: "needs-you", message: "Reinstall DataLab 0.3.0, or run `datalab rollback`." },
    history: [{ at: "2026-09-27T10:00:00+00:00", outcome: "finished", from_version: "0.1.0a1", to_version: "0.1.0a2" }],
    set_aside_notes: [],
    backups: [
      {
        name: "0.1.0a2-20260927-100000-abcdef",
        app_version: "0.1.0a2",
        from_version: "0.1.0a1",
        reason: "restore",
        created_at: "2026-09-27T10:00:00+00:00",
        size_bytes: 3 * 1024 ** 2,
        schema_version: "0008_workflow_runs.sql",
        usable: true,
        kept: true,
      },
    ],
    migrations_applied: 8,
    latest_migration: "0008_workflow_runs.sql",
  };
  mocked.updates.mockResolvedValue(updates);
  wrap(<UpdatesSection />);
  expect(await screen.findByText("Update checks aren't set up yet.")).toBeTruthy();
  expect(screen.getByText("This needs you")).toBeTruthy();
  expect(screen.getByText("Updated")).toBeTruthy();
  expect(screen.getByText("0.1.0a2-20260927-100000-abcdef")).toBeTruthy();
  expect(screen.getByText("kept for good")).toBeTruthy();
});

it("copies the diagnostics, or selects them when the clipboard is refused", async () => {
  mocked.diagnostics.mockResolvedValue({ text: "DataLab diagnostics\nDataLab: 0.1.0a2\n" });
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  wrap(<DiagnosticsSection />);
  fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("DataLab diagnostics\nDataLab: 0.1.0a2\n"));
  expect(await screen.findByText(/Copied/)).toBeTruthy();

  writeText.mockRejectedValueOnce(new Error("denied"));
  fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));
  expect(await screen.findByText(/The text is selected/)).toBeTruthy();
  const box = screen.getByLabelText("Diagnostics") as HTMLTextAreaElement;
  await waitFor(() => expect(document.activeElement).toBe(box));
  expect(box.selectionEnd - box.selectionStart).toBe(box.value.length);
});

it("maps a workflow destination key to an export folder", async () => {
  mocked.destinationKeys.mockResolvedValue([
    { key: "lab-dropbox", used_by: ["weekly.yaml"], destination_id: null, name: null, path: null, available: false },
  ]);
  wrap(<DestinationKeysSection practice={false} />);
  expect(await screen.findByText("no folder yet")).toBeTruthy();
  const select = screen.getByLabelText("Export folder for lab-dropbox") as HTMLSelectElement;
  await waitFor(() => expect(select.disabled).toBe(false));
  fireEvent.change(select, { target: { value: "dest_1" } });
  await waitFor(() => expect(mocked.setDestinationKey).toHaveBeenCalledWith("lab-dropbox", "dest_1"));
});

it("practice has no destination keys to set", () => {
  wrap(<DestinationKeysSection practice />);
  expect(screen.getByText(/delivers workflow results only to its own practice folder/)).toBeTruthy();
  expect(mocked.destinationKeys).not.toHaveBeenCalled();
});

it("formats sizes the way people read them", () => {
  expect(formatBytes(512)).toBe("512 bytes");
  expect(formatBytes(1536)).toBe("1.5 KB");
  expect(formatBytes(250 * 1024 ** 2)).toBe("250 MB");
});
