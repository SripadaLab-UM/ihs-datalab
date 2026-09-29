import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { Chat } from "./Chat";

vi.mock("./useConversationEvents", () => ({ useConversationEvents: () => [] }));
vi.mock("@/api/client", () => ({
  api: {
    modes: vi.fn(async () => [
      { id: "analysis", label: "Analysis", kind: "data", description: "", starters: ["How did steps change?"] },
    ]),
    send: vi.fn(async () => undefined),
  },
}));

// jsdom has no layout, so no scrolling.
Element.prototype.scrollIntoView = vi.fn();

const conversation: Conversation = {
  id: "c1", kind: "data", mode: "analysis", title: "New conversation", model: "gpt-5.5",
  created_at: "", updated_at: "", rigor_review: false, express: false, busy: false,
}; // prettier-ignore

// A starter question runs as it is: picking one shouldn't just fill the box.
it("sends a starter at once, with the effort chosen in the header", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Chat conversation={conversation} />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("How hard the agent thinks"), { target: { value: "high" } });
  fireEvent.click(await screen.findByText("How did steps change?"));
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", "How did steps change?", "high"));
});
