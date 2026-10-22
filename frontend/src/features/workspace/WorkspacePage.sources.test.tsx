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
    computeChoice: vi.fn(),
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
  vi.mocked(api.computeChoice).mockResolvedValue({ high: null });
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

it("shows no data choice at all when this DataLab offers no processed data", async () => {
  // What the backend lists when there's no Dropbox app: no mode offers it.
  vi.mocked(api.modes).mockResolvedValue([mode("analysis", "Analysis", ["database"])]);
  vi.mocked(processedApi.status).mockResolvedValue({ ...processed(false), source: "off", configured: false });
  const dialog = await openDialog();
  await within(dialog).findByText("Analysis");
  expect(within(dialog).queryByRole("radio", { name: /Processed data|Both|Study database/ })).toBeNull();
  expect(within(dialog).queryByText(/Dropbox/)).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: /Start/ }));
  await waitFor(() => expect(api.createConversation).toHaveBeenCalledWith("analysis", undefined, undefined));
});

it("on the lab's DataLab, starts a high-compute conversation when it's chosen and offered", async () => {
  vi.mocked(processedApi.status).mockResolvedValue(processed(true));
  vi.mocked(api.computeChoice).mockResolvedValue({ high: { memory: "64g", cpus: "16", gpu: true } });
  const dialog = await openDialog();
  const high = await within(dialog).findByRole("radio", { name: /High compute/ });
  expect(within(dialog).getByText(/16 CPUs, 64 GB memory, and a GPU/)).toBeTruthy();
  expect(within(dialog).getByRole("radio", { name: /Standard/ })).toBeChecked();
  fireEvent.click(high);
  fireEvent.click(within(dialog).getByRole("button", { name: /Start/ }));
  await waitFor(() =>
    expect(api.createConversation).toHaveBeenCalledWith("analysis", undefined, ["database"], "high"),
  );
});

it("offers no compute choice to someone who can't use high compute", async () => {
  vi.mocked(processedApi.status).mockResolvedValue(processed(true));
  const dialog = await openDialog();
  await within(dialog).findByRole("radio", { name: /Study database/ });
  expect(within(dialog).queryByRole("radio", { name: /High compute/ })).toBeNull();
});
