import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Destination, type FolderTest } from "@/api/client";

import { ExportDestinations } from "./ExportDestinations";

vi.mock("@/api/client", () => ({
  api: {
    destinations: vi.fn(),
    destinationPlaces: vi.fn(),
    addDestination: vi.fn(),
    changeDestination: vi.fn(),
    testDestination: vi.fn(),
    removeDestination: vi.fn(),
  },
}));

const DROPBOX_NOTE =
  "Inside your Dropbox folder: Dropbox will upload it when its app is running and signed in. DataLab can't confirm the upload.";

function folder(overrides: Partial<Destination> = {}): Destination {
  return {
    id: "dest_1",
    name: "Lab Dropbox",
    path: "/Users/me/Library/CloudStorage/Dropbox-UM/IHS",
    where: "~/Library/CloudStorage/Dropbox-UM/IHS",
    available: true,
    status: "ready",
    status_message: null,
    location: "sync_folder",
    sync_provider: "dropbox",
    sync_provider_name: "Dropbox",
    location_note: DROPBOX_NOTE,
    offered: true,
    workflow_keys: [],
    practice: false,
    ...overrides,
  };
}

const PLACES = {
  can_add: true,
  why_not: null,
  places: [
    { id: "dropbox:Dropbox-UM", provider: "dropbox", provider_name: "Dropbox", label: "Dropbox (UM)", where: "~/Library/CloudStorage/Dropbox-UM" },
  ],
} as const;

function show(practice = false) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ExportDestinations practice={practice} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.destinations).mockResolvedValue([]);
  vi.mocked(api.destinationPlaces).mockResolvedValue(PLACES as never);
});

it("adds a folder with a name, the picker opening in the Dropbox it found", async () => {
  vi.mocked(api.addDestination).mockResolvedValue(folder());
  show();
  expect(await screen.findByText("No export folders yet.")).toBeTruthy();
  expect(await screen.findByText(/Found on this computer: Dropbox \(UM\)/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Lab Dropbox" } });
  vi.mocked(api.destinations).mockResolvedValue([folder()]);
  fireEvent.click(screen.getByRole("button", { name: /Choose in Dropbox \(UM\)/ }));
  await waitFor(() => expect(api.addDestination).toHaveBeenCalledWith({ name: "Lab Dropbox", startIn: "dropbox:Dropbox-UM" }));
  const row = await screen.findByRole("listitem", { name: "Lab Dropbox" });
  expect(within(row).getByText("In Dropbox")).toBeTruthy();
  expect(within(row).getByText("Ready")).toBeTruthy();
  expect(within(row).getByText(DROPBOX_NOTE)).toBeTruthy();
  expect(within(row).getByText("~/Library/CloudStorage/Dropbox-UM/IHS")).toBeTruthy();
  // Any folder, too.
  fireEvent.click(screen.getByRole("button", { name: "Choose another folder…" }));
  await waitFor(() => expect(api.addDestination).toHaveBeenLastCalledWith({ name: undefined, startIn: undefined }));
});

it("says when no sync folder was found, and shows why a folder couldn't be added", async () => {
  vi.mocked(api.destinationPlaces).mockResolvedValue({ can_add: true, why_not: null, places: [] });
  vi.mocked(api.addDestination).mockRejectedValue(new Error("DataLab's own data folder can't be attached."));
  show();
  expect(await screen.findByText(/No Dropbox, OneDrive, Box or Google Drive folder was found/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Choose folder…" }));
  expect((await screen.findByRole("alert")).textContent).toContain("DataLab's own data folder");
});

it("Test folder: saved locally, and never claims Dropbox uploaded it", async () => {
  vi.mocked(api.destinations).mockResolvedValue([folder()]);
  const result: FolderTest = {
    ok: true,
    status: "ready",
    message: null,
    test_file: "datalab-test-20260928-101500-abc123.txt",
    removed: true,
    saved_to: "Saved to Lab Dropbox (on this computer)",
    sync_note: "Dropbox will upload it when its app is running and signed in. DataLab can't confirm the upload.",
    destination: folder(),
  };
  vi.mocked(api.testDestination).mockResolvedValue(result);
  show();
  const row = await screen.findByRole("listitem", { name: "Lab Dropbox" });
  fireEvent.click(within(row).getByRole("button", { name: "Test folder" }));
  const outcome = await within(row).findByRole("status");
  expect(outcome.textContent).toContain("Saved locally ✓");
  expect(outcome.textContent).toContain("datalab-test-20260928-101500-abc123.txt");
  expect(outcome.textContent).toContain("removed again");
  expect(outcome.textContent).toContain("DataLab can't confirm the upload.");
  expect(outcome.textContent?.toLowerCase()).not.toMatch(/synced|uploaded/);
  expect(api.testDestination).toHaveBeenCalledWith("dest_1");
});

it("a failed test says why", async () => {
  vi.mocked(api.destinations).mockResolvedValue([folder()]);
  vi.mocked(api.testDestination).mockResolvedValue({
    ok: false,
    status: "not_writable",
    message: "Your account can't save files in this folder.",
    test_file: null,
    removed: true,
    saved_to: null,
    sync_note: null,
    destination: folder(),
  });
  show();
  const row = await screen.findByRole("listitem", { name: "Lab Dropbox" });
  fireEvent.click(within(row).getByRole("button", { name: "Test folder" }));
  expect((await within(row).findByRole("status")).textContent).toBe("Test failed: Your account can't save files in this folder.");
});

it("unavailable folders say what's wrong in plain words", async () => {
  vi.mocked(api.destinations).mockResolvedValue([
    folder({ id: "a", name: "Gone", status: "missing", available: false, status_message: "Check that the Dropbox app is installed and signed in." }),
    folder({ id: "b", name: "Locked", status: "not_writable", available: false, status_message: "Your account can't save files in this folder." }),
    folder({ id: "c", name: "Cloud only", status: "online_only", available: false, status_message: "This folder is online-only in Dropbox." }),
    folder({ id: "d", name: "Inside DataLab", status: "refused", available: false, location: "this_computer", sync_provider: null, sync_provider_name: null, status_message: "This is inside DataLab's own data folder, which can't be an export folder." }), // prettier-ignore
    folder({ id: "e", name: "Old drive", status: "missing", available: false, location: "external_drive", sync_provider: null, sync_provider_name: null, status_message: "The drive this folder is on isn't connected." }), // prettier-ignore
    folder({ id: "f", name: "Paused", offered: false, available: false }),
  ]);
  show();
  const expectations: [string, string, string | null][] = [
    ["Gone", "Not found", "Dropbox app is installed"],
    ["Locked", "Can't save here", "can't save files"],
    ["Cloud only", "Online-only", "online-only in Dropbox"],
    ["Inside DataLab", "Not allowed", "DataLab's own data folder"],
    ["Old drive", "Not found", "isn't connected"],
    ["Paused", "Turned off", null],
  ];
  for (const [name, label, why] of expectations) {
    const row = await screen.findByRole("listitem", { name });
    expect(within(row).getByText(label)).toBeTruthy();
    if (why) expect(within(row).getByText(new RegExp(why))).toBeTruthy();
  }
  expect(within(screen.getByRole("listitem", { name: "Old drive" })).getByText("Separate drive")).toBeTruthy();
});

it("renames a folder", async () => {
  vi.mocked(api.destinations).mockResolvedValue([folder()]);
  vi.mocked(api.changeDestination).mockResolvedValue(folder({ name: "IHS Dropbox" }));
  show();
  const row = await screen.findByRole("listitem", { name: "Lab Dropbox" });
  fireEvent.click(within(row).getByRole("button", { name: "Rename" }));
  const field = within(row).getByLabelText("New name for Lab Dropbox");
  fireEvent.change(field, { target: { value: "IHS Dropbox" } });
  vi.mocked(api.destinations).mockResolvedValue([folder({ name: "IHS Dropbox" })]);
  fireEvent.click(within(row).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.changeDestination).toHaveBeenCalledWith("dest_1", { name: "IHS Dropbox" }));
  expect(await screen.findByRole("listitem", { name: "IHS Dropbox" })).toBeTruthy();
});

it("turns a folder off and on as a destination", async () => {
  vi.mocked(api.destinations).mockResolvedValue([folder({ workflow_keys: ["lab-dropbox"] })]);
  vi.mocked(api.changeDestination).mockResolvedValue(folder({ offered: false }));
  show();
  const row = await screen.findByRole("listitem", { name: "Lab Dropbox" });
  expect(within(row).getByText("lab-dropbox")).toBeTruthy();
  fireEvent.click(within(row).getByRole("checkbox", { name: /Offer for exports and workflow deliveries/ }));
  await waitFor(() => expect(api.changeDestination).toHaveBeenCalledWith("dest_1", { offered: false }));
});

it("removing only forgets the folder, after saying so", async () => {
  vi.mocked(api.destinations).mockResolvedValue([folder()]);
  vi.mocked(api.removeDestination).mockResolvedValue(undefined);
  show();
  const row = await screen.findByRole("listitem", { name: "Lab Dropbox" });
  fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
  const confirm = within(row).getByRole("group", { name: "Confirm remove" });
  expect(confirm.textContent).toContain("The folder and its files aren't touched.");
  expect(api.removeDestination).not.toHaveBeenCalled();
  vi.mocked(api.destinations).mockResolvedValue([]);
  fireEvent.click(within(confirm).getByRole("button", { name: "Forget folder" }));
  await waitFor(() => expect(api.removeDestination).toHaveBeenCalledWith("dest_1"));
  expect(await screen.findByText("No export folders yet.")).toBeTruthy();
});

it("practice: its own folder can be tested; Dropbox setup is on the real DataLab", async () => {
  vi.mocked(api.destinations).mockResolvedValue([
    folder({ id: "practice", name: "Practice exports", practice: true, location: "this_computer", sync_provider: null, sync_provider_name: null, location_note: "Practice DataLab's own folder." }), // prettier-ignore
  ]);
  vi.mocked(api.destinationPlaces).mockResolvedValue({
    can_add: false,
    why_not: "Available on the real DataLab. Practice DataLab saves only to its own practice folder.",
    places: [],
  });
  vi.mocked(api.testDestination).mockResolvedValue({
    ok: true,
    status: "ready",
    message: null,
    test_file: "datalab-test-x.txt",
    removed: true,
    saved_to: "Saved to Practice exports (on this computer)",
    sync_note: null,
    destination: folder(),
  });
  show(true);
  const row = await screen.findByRole("listitem", { name: "Practice exports" });
  expect(within(row).queryByRole("button", { name: "Rename" })).toBeNull();
  expect(within(row).queryByRole("button", { name: "Remove" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Choose/ })).toBeNull();
  expect(screen.getAllByText("Available on the real DataLab").length).toBeGreaterThan(0);
  expect(await screen.findByText(/Practice DataLab saves only to its own practice folder/)).toBeTruthy();
  fireEvent.click(within(row).getByRole("button", { name: "Test folder" }));
  expect((await within(row).findByRole("status")).textContent).toContain("Saved locally ✓");
});
