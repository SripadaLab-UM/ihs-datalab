import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { SettingsPage } from "./SettingsPage";

vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "practice", version: "0.1.0", database_configured: true, catalog_tables: 1234 })),
    lastSafetyReport: vi.fn(async () => null),
    destinations: vi.fn(async () => []),
  },
}));

it("shows each built section, in order, and nothing for the ones still to come", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("1,234 tables and views")).toBeTruthy();
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
    "Safety check",
    "Export folders",
    "About this DataLab",
  ]);
  expect(await screen.findByText(/exports only to its own practice folder/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Add a folder/ })).toBeNull();
  expect(screen.getByText("Not run yet.")).toBeTruthy();
});
