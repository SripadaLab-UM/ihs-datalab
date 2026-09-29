// History says which checkpoints came after a turn asked with Express on.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import type { Conversation } from "@/api/client";

import { SidePanel } from "./SidePanel";

vi.mock("@/api/client", () => {
  const checkpoints = vi.fn(async () => [
    { number: 2, label: "After turn 2", created_at: "2026-09-28T10:00:00Z", turn: 2, express: false, files: 1, bytes: 10, skipped: [] },
    { number: 1, label: "After turn 1", created_at: "2026-09-28T09:00:00Z", turn: 1, express: true, files: 1, bytes: 10, skipped: [] },
  ]);
  // Everything else the panel asks for: nothing.
  const api = new Proxy({ checkpoints } as Record<string, unknown>, {
    get: (target, name: string) => (target[name] ??= vi.fn(async () => [])),
  });
  return { api };
});

const conversation: Conversation = {
  id: "c1", kind: "data", mode: "analysis", title: "t", model: "m",
  created_at: "", updated_at: "", rigor_review: false, express: false, busy: false,
}; // prettier-ignore

it("labels the checkpoint after an Express turn", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SidePanel conversation={conversation} onOpen={() => undefined} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  const first = (await screen.findByText("After turn 1")).closest("li")!;
  const second = screen.getByText("After turn 2").closest("li")!;
  expect(within(first).getByText("Express")).toBeTruthy();
  expect(within(second).queryByText("Express")).toBeNull();
});
