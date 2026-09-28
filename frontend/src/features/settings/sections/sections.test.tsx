import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import type { Connections, PracticeDatabase, Storage, StorageItem, UpdateCheck, Updates } from "@/api/settings";
import { formatBytes, settingsApi } from "@/api/settings";

import { ConnectionsSection } from "./ConnectionsSection";
import { DestinationKeysSection } from "./DestinationKeysSection";
import { DiagnosticsSection } from "./DiagnosticsSection";
import { untilSettled } from "./PracticeDatabase";
import { StorageSection } from "./StorageSection";
import { UpdatesSection } from "./UpdatesSection";

// GitHub has its own tests (GitHubSection.test.tsx).
vi.mock("./GitHubSection", () => ({ GitHubSection: () => null }));

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
    practiceDatabase: vi.fn(),
    startPracticeDatabase: vi.fn(),
    resetPracticeDatabase: vi.fn(),
    storage: vi.fn(),
    removeStorageItem: vi.fn(),
    updates: vi.fn(),
    updateCheck: vi.fn(),
    checkForUpdates: vi.fn(),
    installUpdate: vi.fn(),
    diagnostics: vi.fn(),
    destinationKeys: vi.fn(),
    setDestinationKey: vi.fn(async () => undefined),
  },
}));

const mocked = vi.mocked(settingsApi);

function wrap(children: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
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
  expect(await screen.findByText("Not saved")).toBeTruthy();
  // The real profile: the key is saved and can be replaced; nothing is fixed.
  expect(within(screen.getByRole("region", { name: "U-M GPT" })).getByText("Saved in keychain")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Replace key" })).toBeTruthy();
  expect(screen.queryByText(/Fixed on the practice/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  const field = screen.getByLabelText("Database password for SVC_READER") as HTMLInputElement;
  expect(field.type).toBe("password");
  fireEvent.change(field, { target: { value: "s3cret" } });
  fireEvent.click(screen.getByRole("button", { name: "Save to keychain" }));
  await waitFor(() => expect(mocked.saveDatabasePassword).toHaveBeenCalledWith("s3cret", expect.anything()));
  expect(await screen.findByText("Saved")).toBeTruthy();
  expect(screen.queryByLabelText("Database password for SVC_READER")).toBeNull();
  expect(document.body.textContent).not.toContain("s3cret");
  expect(screen.getByRole("button", { name: "Replace key" })).toBeTruthy();
});

it("tests the connection and says how it went", async () => {
  mocked.connections.mockResolvedValue(REAL);
  mocked.testConnections.mockResolvedValue({
    database: { ok: false, message: "Can't reach the database. Connect to the U-M VPN (or check your network), then test again. (DNS lookup failed)", enabled_roles: [], read_only: null },
    model: { ok: true, message: "U-M GPT accepted the key and offers 3 approved models." },
  });
  wrap(<ConnectionsSection />);
  await screen.findByText("Not saved");
  fireEvent.click(screen.getAllByRole("button", { name: "Test connection" })[0]);
  expect(await screen.findByText(/Connect to the U-M VPN/)).toBeTruthy();
  expect(screen.getByText(/offers 3 approved models/)).toBeTruthy();
});

const PRACTICE: Connections = {
  ...REAL,
  profile: "practice",
  oracle: { ...REAL.oracle, practice: true, password: null, can_set_password: false },
  model: { ...REAL.model, can_set_key: false },
  read_only_because: "Practice DataLab uses the local synthetic database, whose settings are fixed.",
};

const PRACTICE_DB: PracticeDatabase = {
  phase: "ready",
  message: "The practice database is running, with its made-up data.",
  container: "datalab-practice-oracle",
  volume: "datalab-practice-oracle-data",
  port: 1522,
  managed: true,
  busy: false,
  cant_reset_because: null,
};

it("practice shows its synthetic database and offers no changes", async () => {
  mocked.connections.mockResolvedValue(PRACTICE);
  mocked.practiceDatabase.mockResolvedValue(PRACTICE_DB);
  wrap(<ConnectionsSection />);
  expect(await screen.findByText(/whose settings are fixed/)).toBeTruthy();
  expect(screen.getByText("None to enter: DataLab saved the synthetic database's own")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^(Save|Replace) (key|password)$/ })).toBeNull();
  // What practice fixes is labelled, not missing, and can still be tested.
  expect(screen.getByText("Fixed on the practice DataLab: the synthetic database")).toBeTruthy();
  expect(screen.getByText("Fixed on the practice DataLab")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Test connection" })).toHaveLength(2);
  expect(screen.getByText("Saved in keychain")).toBeTruthy();
});

it("says which features need the U-M GPT key and which don't", async () => {
  mocked.connections.mockResolvedValue(PRACTICE);
  mocked.practiceDatabase.mockResolvedValue(PRACTICE_DB);
  wrap(<ConnectionsSection />);
  const umgpt = within(await screen.findByRole("region", { name: "U-M GPT" }));
  expect(umgpt.getByText("The key is optional on the practice DataLab.")).toBeTruthy();
  expect(umgpt.getByText("Conversations with the agent, in the Workspace, and their automatic titles")).toBeTruthy();
  expect(umgpt.getByText("The Safety check's model checks")).toBeTruthy();
  expect(umgpt.getByText(/Edit with agent, and the agent's suggested updates \(in the real DataLab\)/)).toBeTruthy();
  expect(umgpt.getByText("The SQL Playground")).toBeTruthy();
  expect(umgpt.getByText("Save as workflow, and running workflows")).toBeTruthy();
});

it("practice says how its database stands and resets it only once confirmed", async () => {
  mocked.connections.mockResolvedValue(PRACTICE);
  mocked.practiceDatabase.mockResolvedValue(PRACTICE_DB);
  mocked.resetPracticeDatabase.mockResolvedValue({ ...PRACTICE_DB, phase: "resetting", busy: true });
  wrap(<ConnectionsSection />);
  expect(await screen.findByText("Running")).toBeTruthy();
  expect(screen.getByText(/with its made-up data/)).toBeTruthy();
  expect(screen.getByText("datalab-practice-oracle-data")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reset practice data…" }));
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText(/sets it up again from scratch/)).toBeTruthy();
  fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
  expect(mocked.resetPracticeDatabase).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Reset practice data…" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reset" }));
  await waitFor(() => expect(mocked.resetPracticeDatabase).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("Resetting…")).toBeTruthy();
});

it("a practice database problem is said, with Try again", async () => {
  mocked.connections.mockResolvedValue(PRACTICE);
  mocked.practiceDatabase.mockResolvedValue({
    ...PRACTICE_DB,
    phase: "problem",
    message: "Docker Desktop isn't running, and the practice database runs in it.",
  });
  mocked.startPracticeDatabase.mockResolvedValue({ ...PRACTICE_DB, phase: "starting" });
  wrap(<ConnectionsSection />);
  expect(await screen.findByText("Not running")).toBeTruthy();
  expect(screen.getByText(/Docker Desktop isn't running/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(mocked.startPracticeDatabase).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("Starting…")).toBeTruthy();
});

it("while Docker Desktop starts, it says so and keeps asking until the database is up", async () => {
  mocked.connections.mockResolvedValue(PRACTICE);
  mocked.practiceDatabase.mockResolvedValue({
    ...PRACTICE_DB,
    phase: "waiting-for-docker",
    message: "Docker Desktop isn't running. DataLab carries on by itself once it is.",
  });
  wrap(<ConnectionsSection />);
  expect(await screen.findByText("Waiting for Docker…")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  // Not settled: asked again every 3 s, until it's running (or has a problem).
  expect(untilSettled({ ...PRACTICE_DB, phase: "waiting-for-docker" })).toBe(3000);
  expect(untilSettled({ ...PRACTICE_DB, phase: "loading" })).toBe(3000);
  expect(untilSettled(PRACTICE_DB)).toBe(false);
  expect(untilSettled({ ...PRACTICE_DB, phase: "problem" })).toBe(false);
});

it("the real profile shows no practice database", async () => {
  mocked.connections.mockResolvedValue(REAL);
  wrap(<ConnectionsSection />);
  await screen.findByText("Not saved");
  expect(mocked.practiceDatabase).not.toHaveBeenCalled();
  expect(screen.queryByText(/Reset practice data/)).toBeNull();
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

const IDLE = { state: "idle", version: null, message: "", started_at: null, updated_at: null } as const;

const UP_TO_DATE: UpdateCheck = {
  state: "up-to-date",
  message: "DataLab 0.1.0a2 is the newest release or pre-release.",
  current_version: "0.1.0a2",
  channel: "auto",
  checked_at: "2026-09-27T10:00:00+00:00",
  available: null,
  can_install: false,
  cannot_install_because: null,
  install: IDLE,
  updating: false,
};

const AVAILABLE: UpdateCheck = {
  ...UP_TO_DATE,
  state: "available",
  message: "DataLab 0.1.0a3 is available.",
  available: {
    version: "0.1.0a3",
    tag: "v0.1.0-alpha.3",
    title: "DataLab v0.1.0-alpha.3",
    notes: "Faster exports.\n<img src=x onerror=alert(1)>",
    published_at: "2026-09-27T12:00:00Z",
    page: "https://github.com/SripadaLab-UM/ihs-datalab/releases/tag/v0.1.0-alpha.3",
    prerelease: true,
    size_bytes: 500_000,
  },
  can_install: true,
};

function updatesWith(check: UpdateCheck): Updates {
  return {
    version: "0.1.0a2",
    check,
    marker: null,
    marker_unreadable: false,
    recovery: null,
    history: [],
    set_aside_notes: [],
    backups: [],
    migrations_applied: 8,
    latest_migration: "0008_workflow_runs.sql",
  };
}

it("offers a newer release with its notes as text, and installs only once confirmed", async () => {
  mocked.updates.mockResolvedValue(updatesWith(AVAILABLE));
  mocked.updateCheck.mockResolvedValue(AVAILABLE);
  mocked.installUpdate.mockResolvedValue({
    ...AVAILABLE,
    install: { ...IDLE, state: "downloading", version: "0.1.0a3", message: "Downloading DataLab 0.1.0a3…" },
  });
  const { container } = wrap(<UpdatesSection />);
  expect(await screen.findByText("DataLab v0.1.0-alpha.3")).toBeTruthy();
  // The notes are shown as written, never as HTML.
  expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByRole("link", { name: "on GitHub" }).getAttribute("href")).toContain("/releases/tag/");

  fireEvent.click(screen.getByRole("button", { name: "Install update…" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/installs DataLab 0.1.0a3 beside 0.1.0a2, which is kept/)).toBeTruthy();
  expect(mocked.installUpdate).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(mocked.installUpdate).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Install update…" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Install and restart" }));
  await waitFor(() => expect(mocked.installUpdate).toHaveBeenCalledWith("0.1.0a3"));
  expect(await screen.findByText("Downloading and checking the new version (DataLab 0.1.0a3)")).toBeTruthy();
});

it("says why an update can't be installed here", async () => {
  const cant = {
    ...AVAILABLE,
    can_install: false,
    cannot_install_because: "This copy of DataLab wasn't installed by the DataLab installer.",
  };
  mocked.updates.mockResolvedValue(updatesWith(cant));
  mocked.updateCheck.mockResolvedValue(cant);
  wrap(<UpdatesSection />);
  expect(await screen.findByText(/wasn't installed by the DataLab installer/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Install update…" }) as HTMLButtonElement).disabled).toBe(true);
});

it("checks now, and shows being offline as a plain message", async () => {
  const offline: UpdateCheck = {
    ...UP_TO_DATE,
    state: "offline",
    message: "Couldn't reach GitHub to check for updates (no internet connection?).",
  };
  mocked.updates.mockResolvedValue(updatesWith(UP_TO_DATE));
  mocked.updateCheck.mockResolvedValue(UP_TO_DATE);
  mocked.checkForUpdates.mockResolvedValue(offline);
  wrap(<UpdatesSection />);
  expect(await screen.findByText(/is the newest release or pre-release/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Check now" }));
  expect(await screen.findByText(/Couldn't reach GitHub/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Install update…" })).toBeNull();
});

it("says when an update didn't happen", async () => {
  const failed: UpdateCheck = {
    ...AVAILABLE,
    install: { ...IDLE, state: "failed", version: "0.1.0a3", message: "DataLab 0.1.0a2 is still the one in use." },
  };
  mocked.updates.mockResolvedValue(updatesWith(failed));
  mocked.updateCheck.mockResolvedValue(failed);
  wrap(<UpdatesSection />);
  expect(await screen.findByText("The update didn't happen")).toBeTruthy();
  expect(screen.getByText(/is still the one in use/)).toBeTruthy();
});

it("updates show the version, what needs you, and the backups", async () => {
  mocked.updateCheck.mockResolvedValue(UP_TO_DATE);
  const updates: Updates = {
    version: "0.1.0a2",
    check: UP_TO_DATE,
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
  expect(await screen.findByText(/is the newest release or pre-release/)).toBeTruthy();
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

it("warns that saving won't take effect while an environment variable sets the secret", async () => {
  mocked.connections.mockResolvedValue({ ...REAL, oracle: { ...REAL.oracle, password: "environment" } });
  wrap(<ConnectionsSection />);
  expect(await screen.findByText(/saving here won't take effect/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Replace password" }));
  const field = screen.getByLabelText("Database password for SVC_READER") as HTMLInputElement;
  expect(field.autocomplete).toBe("new-password");
  expect(screen.getByText(/saving here won't take effect/)).toBeTruthy();
});

it("says updates aren't set up until a release key is pinned, with nothing to press", async () => {
  const off: UpdateCheck = {
    ...UP_TO_DATE,
    state: "not-configured",
    message: "Updates aren't set up in this DataLab: it has no release signing key.",
    checked_at: null,
  };
  mocked.updates.mockResolvedValue(updatesWith(off));
  mocked.updateCheck.mockResolvedValue(off);
  wrap(<UpdatesSection />);
  expect(await screen.findByText(/Updates aren't set up/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Check now" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Install update…" })).toBeNull();
});
