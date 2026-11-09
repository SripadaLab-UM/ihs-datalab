import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type Health } from "@/api/client";

import { CATALOG_CHECK_MS, CATALOG_WAIT_MS, useCatalogAfterSignIn } from "./catalogRefresh";

vi.mock("@/api/client", () => ({ api: { health: vi.fn() } }));

const health = (more: Partial<Health>): Health => ({
  status: "ok", version: "0.4.0", profile: "real", database_configured: true, catalog_tables: 0,
  deployment: "server", catalog_state: "empty", ...more,
}); // prettier-ignore

function Watcher() {
  useCatalogAfterSignIn();
  return null;
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <Watcher />
    </QueryClientProvider>,
  );
  const keys = () => invalidated.mock.calls.map(([filters]) => (filters?.queryKey as string[])[0]);
  return { keys };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.health).mockReset();
});
afterEach(() => vi.useRealTimers());

it("on the server, asks again until the sign-in's pull brings the catalog, then refreshes what shows it", async () => {
  vi.mocked(api.health)
    .mockResolvedValueOnce(health({}))
    .mockResolvedValueOnce(health({}))
    .mockResolvedValue(health({ catalog_tables: 909, catalog_state: "ready" }));
  const { keys } = show();
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(api.health).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(CATALOG_CHECK_MS));
  expect(keys()).toEqual([]);
  await act(() => vi.advanceTimersByTimeAsync(CATALOG_CHECK_MS));
  expect(keys()).toEqual(expect.arrayContaining(["catalog-status", "sql-catalog", "knowledge-status", "pipelines-status"]));
  const asked = vi.mocked(api.health).mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(10 * CATALOG_CHECK_MS));
  expect(vi.mocked(api.health).mock.calls.length).toBe(asked); // it's there: no more asking
});

it("stops asking after a while if the catalog never comes", async () => {
  vi.mocked(api.health).mockResolvedValue(health({}));
  show();
  await act(() => vi.advanceTimersByTimeAsync(CATALOG_WAIT_MS + CATALOG_CHECK_MS));
  const asked = vi.mocked(api.health).mock.calls.length;
  expect(asked).toBeGreaterThan(10);
  await act(() => vi.advanceTimersByTimeAsync(10 * CATALOG_CHECK_MS));
  expect(vi.mocked(api.health).mock.calls.length).toBe(asked);
});

it("on a person's own computer, never asks again on its own", async () => {
  vi.mocked(api.health).mockResolvedValue(health({ deployment: "desktop" }));
  show();
  await act(() => vi.advanceTimersByTimeAsync(10 * CATALOG_CHECK_MS));
  expect(api.health).toHaveBeenCalledTimes(1);
});
