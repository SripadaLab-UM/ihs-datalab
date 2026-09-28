import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";

import type { Conversation } from "@/api/client";

import { Chat } from "./Chat";
import { showStep } from "./showStep";

const events = vi.hoisted(() => [
  { seq: 1, type: "user_message", data: { text: "How many steps?" } },
  { seq: 2, type: "command_started", data: { id: "c1", command: "/bin/bash -lc 'ls /work'" } },
  { seq: 3, type: "command_finished", data: { id: "c1", exit_code: 0, status: "completed" } },
  { seq: 4, type: "command_started", data: { id: "c2", command: "/bin/bash -lc \"python3 -c 'print(40 + 2)'\"" } },
  { seq: 5, type: "command_finished", data: { id: "c2", exit_code: 0, status: "completed" } },
  { seq: 6, type: "answer", data: { id: "a", text: "About 42.", phase: "final_answer" } },
  { seq: 7, type: "turn_finished", data: { status: "completed" } },
  { seq: 8, type: "turn_done", data: {} },
]);
vi.mock("./useConversationEvents", () => ({ useConversationEvents: () => events }));
vi.mock("@/api/client", () => ({ api: { conversations: vi.fn(async () => []), files: vi.fn(async () => []) } }));

const scrolled = vi.fn();
Element.prototype.scrollIntoView = scrolled;

it("opens How this answer was made at the step the Code tab links to", async () => {
  const conversation = { id: "c", title: "Steps", kind: "data", mode: "analysis", model: "m", busy: false } as Conversation;
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <Chat conversation={conversation} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("About 42.")).toBeTruthy();
  expect(screen.queryByText("Ran a short Python snippet")).toBeNull(); // folded away
  // A step no turn has changes nothing.
  act(() => showStep("cmd-nope"));
  expect(screen.queryByText("Ran a short Python snippet")).toBeNull();
  scrolled.mockClear();
  act(() => showStep("cmd-c2"));
  const step = await screen.findByRole("button", { name: /Ran a short Python snippet/ });
  expect(step).toHaveAttribute("aria-expanded", "true");
  expect(document.activeElement).toHaveAttribute("id", "step-cmd-c2");
  expect(scrolled).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /Looked at the files/ })).toHaveAttribute("aria-expanded", "false");
});
