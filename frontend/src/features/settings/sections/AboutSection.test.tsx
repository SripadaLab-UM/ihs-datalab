import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Health } from "@/api/client";

import { AboutSection } from "./AboutSection";

vi.mock("@/api/client", () => ({ api: { catalogStatus: vi.fn() } }));

const health: Health = {
  status: "ok",
  version: "0.1.0",
  profile: "practice",
  database_configured: true,
  catalog_tables: 0,
};

function wrap(children: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

beforeEach(() => vi.mocked(api.catalogStatus).mockReset());

it("says why the catalog is empty, and what happens next, from the signed-in route", async () => {
  const detail = "DataLab builds it (metadata only) the first time it connects to the database.";
  vi.mocked(api.catalogStatus).mockResolvedValue({ state: "building", tables: 0, detail });
  render(wrap(<AboutSection health={{ ...health, catalog_state: "building" }} />));
  expect(await screen.findByText(detail)).toBeTruthy();
});

it("asks for nothing more once the catalog has tables", () => {
  render(wrap(<AboutSection health={{ ...health, catalog_tables: 12, catalog_state: "ready" }} />));
  expect(screen.getByText("12 tables and views")).toBeTruthy();
  expect(api.catalogStatus).not.toHaveBeenCalled();
});
