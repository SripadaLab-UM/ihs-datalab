import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { type Connections, settingsApi } from "@/api/settings";

import { WorkspacePage } from "./WorkspacePage";

vi.mock("@/api/client", () => ({
  api: { conversations: vi.fn(), modes: vi.fn(), models: vi.fn(), health: vi.fn() },
}));
vi.mock("@/api/settings", async (original) => ({
  ...(await original<typeof import("@/api/settings")>()),
  settingsApi: { connections: vi.fn() },
}));
vi.mock("@/components/chat/DockedChat", () => ({ DockedChat: () => <div>chat</div> }));

function connections(key: Connections["model"]["key"]): Connections {
  return {
    profile: "practice",
    settings_file: "settings.toml",
    oracle: {
      configured: true,
      practice: true,
      dsn: "127.0.0.1:1522/FREEPDB1",
      user: "DATALAB_RO",
      read_only_roles: [],
      allowed_schemas: [],
      password: null,
      can_set_password: false,
    },
    model: { base_url: "https://api.example/v1", key, can_set_key: false },
    read_only_because: null,
  };
}

function open() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <Routes>
          <Route path="workspace" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.conversations).mockResolvedValue([]);
});

it("without a key, the first screen says what still works", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice", practice_database: "ready" } as never);
  vi.mocked(settingsApi.connections).mockResolvedValue(connections("missing"));
  open();
  expect(await screen.findByText(/conversations with the agent can't start yet/)).toBeTruthy();
  expect(screen.getByText(/The SQL Playground, workflows and exports work without one/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "What the key is for" })).toBeTruthy();
});

it("says while practice's database is starting", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice", practice_database: "loading" } as never);
  vi.mocked(settingsApi.connections).mockResolvedValue(connections("keychain"));
  open();
  expect(await screen.findByText(/DataLab is starting the practice database/)).toBeTruthy();
  expect(screen.queryByText(/conversations with the agent can't start/)).toBeNull();
});

it("says nothing more once all is well", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice", practice_database: "ready" } as never);
  vi.mocked(settingsApi.connections).mockResolvedValue(connections("keychain"));
  open();
  await screen.findByText("Ask the IHS data a question.");
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByText(/U-M GPT key/)).toBeNull();
});
