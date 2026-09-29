import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { DeleteConversation } from "./DeleteConversation";

vi.mock("@/api/client", () => ({ api: { deleteConversation: vi.fn() } }));

const conversation = (busy: boolean): Conversation => ({
  id: "c1", kind: "data", mode: "analysis", title: "Sleep and mood", model: "gpt-5.5",
  created_at: "", updated_at: "", rigor_review: false, express: false, busy,
}); // prettier-ignore

function show(busy: boolean, onDeleted = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DeleteConversation conversation={conversation(busy)} onClose={vi.fn()} onDeleted={onDeleted} />
    </QueryClientProvider>,
  );
  return onDeleted;
}

it("deletes only after the person confirms, and says what stays", async () => {
  vi.mocked(api.deleteConversation).mockResolvedValue(undefined as never);
  const onDeleted = show(false);
  expect(screen.getByText(/aren't touched/)).toBeTruthy();
  expect(api.deleteConversation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  expect(api.deleteConversation).toHaveBeenCalledWith("c1");
});

it("won't delete while the agent is working", () => {
  show(true);
  expect((screen.getByRole("button", { name: /Delete/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/Stop it first/)).toBeTruthy();
});
