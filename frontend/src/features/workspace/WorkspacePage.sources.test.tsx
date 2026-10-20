import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Mode } from "@/api/client";
import { type Processed, processedApi } from "@/api/processed";

import { WorkspacePage } from "./WorkspacePage";

vi.mock("@/api/client", () => ({
  api: {
    conversations: vi.fn(),
    modes: vi.fn(),
    models: vi.fn(),
    health: vi.fn(),
    createConversation: vi.fn(),
  },
}));
vi.mock("@/api/processed", () => ({ processedApi: { status: vi.fn() } }));
vi.mock("@/components/chat/DockedChat", () => ({ DockedChat: () => <div>chat</div> }));

const mode = (id: string, label: string, sources: Mode["sources"]): Mode => ({
  id, label, kind: "data", description: `${label} work.`, starters: ["?"], tab_only: false, queries: true, attachments: true, question: "?", remember: false, sources,
}); // prettier-ignore

const processed = (ready: boolean): Processed => ({
  source: "dropbox", configured: true, ready, problem: ready ? null : "Connect Dropbox in Settings → Processed data.",
  folder: ready ? "/Study Data" : null, files: 10, bytes: 100, indexed_at: null, sign_in: null,
}); // prettier-ignore

beforeEach(() => {
  vi.mocked(api.conversations).mockResolvedValue([]);
  vi.mocked(api.health).mockResolvedValue({ profile: "real" } as never);
  vi.mocked(api.models).mockResolvedValue({ default: "gpt-5.5", available: [] });
  vi.mocked(api.modes).mockResolvedValue([
    mode("analysis", "Analysis", ["database", "dropbox"]),
    { ...mode("research", "Research", []), kind: "research" },
  ]);
  vi.mocked(api.createConversation).mockResolvedValue({ id: "c1" } as never);
});

async function openDialog() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <Routes>
          <Route path="workspace" element={<WorkspacePage />} />
          <Route path="workspace/:id" element={<div>opened</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click((await screen.findAllByRole("button", { name: /New conversation/ }))[0]);
  return screen.findByRole("dialog", { name: "New conversation" });
}

it("starts a conversation on the processed data and the database when Both is chosen", async () => {
  vi.mocked(processedApi.status).mockResolvedValue(processed(true));
  const dialog = await openDialog();
  const both = await within(dialog).findByRole("radio", { name: /Both/ });
  await waitFor(() => expect(both).toBeEnabled());
  fireEvent.click(both);
  fireEvent.click(within(dialog).getByRole("button", { name: /Start/ }));
  await waitFor(() =>
    expect(api.createConversation).toHaveBeenCalledWith("analysis", undefined, ["database", "dropbox"]),
  );
});

it("offers only the database until Dropbox is set up, and says how", async () => {
  vi.mocked(processedApi.status).mockResolvedValue(processed(false));
  const dialog = await openDialog();
  expect(await within(dialog).findByText(/Connect Dropbox in Settings/)).toBeTruthy();
  expect(within(dialog).getByRole("radio", { name: /Processed data/ })).toBeDisabled();
  expect(within(dialog).getByRole("radio", { name: /Study database/ })).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: /Start/ }));
  await waitFor(() => expect(api.createConversation).toHaveBeenCalledWith("analysis", undefined, ["database"]));
});
