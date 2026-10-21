import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { type Processed, processedApi } from "@/api/processed";

import { ProcessedDataSection } from "./ProcessedDataSection";

vi.mock("@/api/processed", () => ({
  processedApi: { status: vi.fn(), folders: vi.fn(), choose: vi.fn(), refresh: vi.fn(), connect: vi.fn() },
}));

const signedIn: Processed = {
  source: "dropbox", configured: true, ready: false, problem: "Choose the lab's processed-data folder.",
  folder: null, files: 0, bytes: 0, indexed_at: null, listing: null, listing_error: null,
  sign_in: { state: "signed in", account: "Test Person", authorize_url: null, message: null },
}; // prettier-ignore

beforeEach(() => {
  vi.mocked(processedApi.status).mockResolvedValue(signedIn);
  vi.mocked(processedApi.folders).mockImplementation(async (inside?: string) =>
    inside === "id:mine"
      ? [{ id: "id:test", name: "DataLab test (synthetic)", path: "/Person/DataLab test (synthetic)" }]
      : [
          { id: "id:lab", name: "Lab Study Data", path: "/Lab Study Data" },
          { id: "id:mine", name: "Person", path: "/Person" },
        ],
  );
  vi.mocked(processedApi.choose).mockResolvedValue({ ...signedIn, ready: true, folder: "/Person/DataLab test (synthetic)" });
});

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <ProcessedDataSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("opens a folder to choose one inside it, and can go back up", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Open Person" }));
  await screen.findByText("DataLab test (synthetic)");
  expect(processedApi.folders).toHaveBeenLastCalledWith("id:mine");
  expect(screen.queryByText("Lab Study Data")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Use DataLab test (synthetic)" }));
  await waitFor(() => expect(processedApi.choose).toHaveBeenCalledWith("id:test"));
});

it("goes back to the top from the folder path", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Open Person" }));
  await screen.findByText("DataLab test (synthetic)");
  fireEvent.click(screen.getByRole("button", { name: "Dropbox" }));
  expect(await screen.findByText("Lab Study Data")).toBeTruthy();
});

it("shows how far a listing has got, and stops asking once it's done", async () => {
  const listing = { files: 12400, folders_done: 23, folders_total: 65, seconds: 70 };
  vi.mocked(processedApi.status)
    .mockResolvedValueOnce({ ...signedIn, ready: true, problem: null, folder: "/Lab Study Data", listing })
    .mockResolvedValue({
      ...signedIn, ready: true, problem: null, folder: "/Lab Study Data",
      files: 50614, bytes: 1_400_000_000_000, indexed_at: new Date().toISOString(),
    }); // prettier-ignore
  show();
  expect(await screen.findByText("Listing… 12,400 files so far · 23 of 65 folders · 1 min 10 s")).toBeTruthy();
  expect(screen.getByRole("progressbar", { name: "Folders listed" })).toHaveAttribute("value", "23");
  expect(screen.getByRole("button", { name: "Listing…" })).toBeDisabled();
  // It asks again while listing, then shows the list.
  expect(await screen.findByText(/^50,614 files/, {}, { timeout: 3000 })).toBeTruthy();
  const asked = vi.mocked(processedApi.status).mock.calls.length;
  await new Promise((done) => setTimeout(done, 1500));
  expect(vi.mocked(processedApi.status).mock.calls.length).toBe(asked);
});

it("shows nothing when this DataLab has no Dropbox app", async () => {
  vi.mocked(processedApi.status).mockResolvedValue({
    ...signedIn, source: "off", configured: false, sign_in: null,
    problem: "The lab's Dropbox app isn't set in settings.toml ([dropbox] app_key).",
  }); // prettier-ignore
  show();
  await waitFor(() => expect(processedApi.status).toHaveBeenCalled());
  await waitFor(() => expect(screen.queryByText("Checking…")).toBeNull());
  expect(screen.queryByText(/Processed data/)).toBeNull();
  expect(screen.queryByText(/Dropbox/)).toBeNull();
});
