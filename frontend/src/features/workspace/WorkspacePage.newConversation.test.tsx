import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Mode } from "@/api/client";

import { WorkspacePage } from "./WorkspacePage";

vi.mock("@/api/client", () => ({
  api: {
    conversations: vi.fn(),
    modes: vi.fn(),
    models: vi.fn(),
    health: vi.fn(),
  },
}));
vi.mock("@/components/chat/DockedChat", () => ({ DockedChat: () => <div>chat</div> }));

const mode = (id: string, label: string, extra: Partial<Mode> = {}): Mode => ({
  id, label, kind: "data", description: `${label} work.`, starters: ["?"], tab_only: false, queries: true, attachments: true, ...extra,
}); // prettier-ignore

beforeEach(() => {
  vi.mocked(api.conversations).mockResolvedValue([]);
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
  vi.mocked(api.models).mockResolvedValue({ default: "gpt-5.5", available: [] });
  vi.mocked(api.modes).mockResolvedValue([
    mode("analysis", "Analysis"),
    mode("extraction", "Data extraction"),
    mode("engineering", "Data engineering"),
    mode("workflows", "Workflow authoring", { tab_only: true }),
    mode("knowledge", "Knowledge writing", { tab_only: true, queries: false }),
    mode("research", "Research", { kind: "research" }),
  ]);
});

it("offers the modes a conversation starts in, not the ones a tab docks", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <Routes>
          <Route path="workspace" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click((await screen.findAllByRole("button", { name: /New conversation/ }))[0]);
  const dialog = await screen.findByRole("dialog", { name: "New conversation" });
  await within(dialog).findByRole("button", { name: /Analysis/ });
  const offered = within(dialog)
    .getAllByRole("button", { pressed: false })
    .concat(within(dialog).getAllByRole("button", { pressed: true }))
    .map((b) => b.textContent ?? "");
  for (const label of ["Analysis", "Data extraction", "Data engineering", "Research"]) {
    expect(offered.some((text) => text.includes(label))).toBe(true);
  }
  expect(within(dialog).queryByText("Workflow authoring")).toBeNull();
  expect(within(dialog).queryByText("Knowledge writing")).toBeNull();
});
