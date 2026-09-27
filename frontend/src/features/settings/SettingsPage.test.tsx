import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";

import { SettingsPage } from "./SettingsPage";

vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "practice", version: "0.1.0", database_configured: true, catalog_tables: 1234 })),
    lastSafetyReport: vi.fn(async () => null),
    destinations: vi.fn(async () => []),
  },
}));

vi.mock("@/api/settings", async (original) => ({
  ...(await original<typeof import("@/api/settings")>()),
  settingsApi: {
    connections: vi.fn(() => new Promise(() => {})),
    storage: vi.fn(() => new Promise(() => {})),
    updates: vi.fn(() => new Promise(() => {})),
    destinationKeys: vi.fn(async () => []),
  },
}));

it("shows each built section, in order, and nothing for the ones still to come", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("1,234 tables and views")).toBeTruthy();
  expect(await screen.findByText(/delivers workflow results only to its own practice folder/)).toBeTruthy();
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
    "Safety check",
    "Connections",
    "Export folders",
    "Workflow destinations",
    "Storage",
    "Updates",
    "Diagnostics",
    "About this DataLab",
  ]);
  expect(await screen.findByText(/exports only to its own practice folder/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Add a folder/ })).toBeNull();
  expect(screen.getByText("Not run yet.")).toBeTruthy();
  // Other screens link here: /settings#destination-keys.
  expect(document.getElementById("destination-keys")).toBeTruthy();
});

it("ignores a malformed link to a section", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/settings#%E0%A4%A"]}>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("1,234 tables and views")).toBeTruthy();
});
