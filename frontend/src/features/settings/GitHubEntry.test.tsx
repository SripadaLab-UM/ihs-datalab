import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { type GitHubStatus, githubApi } from "@/api/github";

import { GitHubEntry } from "./GitHubEntry";

vi.mock("@/api/client", () => ({ api: { health: vi.fn() } }));
vi.mock("@/api/github", () => ({ githubApi: { status: vi.fn() } }));

const health = (profile: "real" | "practice") =>
  ({ profile, version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 1 }) as never;
const status = (more: Partial<GitHubStatus> = {}): GitHubStatus => ({
  available: true,
  message: null,
  signed_in: false,
  account: null,
  repos: [{ area: "knowledge", name: "SripadaLab-UM/ihs-knowledge" }],
  ...more,
});

function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <GitHubEntry />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.health).mockReset().mockResolvedValue(health("real"));
  vi.mocked(githubApi.status).mockReset().mockResolvedValue(status());
});

it("shows who is signed in, linking to Settings → Connections", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(status({ signed_in: true, account: { login: "yfang", name: "Yu Fang" } }));
  show();
  const link = await screen.findByRole("link", { name: "GitHub: signed in as yfang" });
  expect(link.textContent).toBe("yfang");
  expect(link.getAttribute("href")).toBe("/settings/connections#github");
});

it("offers Sign in when nobody is", async () => {
  show();
  const link = await screen.findByRole("link", { name: "Sign in to GitHub" });
  expect(link.textContent).toBe("Sign in");
  expect(link.getAttribute("href")).toBe("/settings/connections#github");
});

it("isn't there on practice, which never signs in to GitHub", async () => {
  vi.mocked(api.health).mockResolvedValue(health("practice"));
  const { container } = show();
  await waitFor(() => expect(api.health).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 0));
  expect(container.textContent).toBe("");
  expect(githubApi.status).not.toHaveBeenCalled();
});

it("isn't there when the lab's repos aren't set up", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(status({ available: false, repos: [] }));
  const { container } = show();
  await waitFor(() => expect(githubApi.status).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 0));
  expect(container.textContent).toBe("");
});
