import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { Title, titleToSend } from "./Chat";

vi.mock("@/api/client", () => ({ api: { rename: vi.fn() } }));

const untitled = { id: "c", title: "New conversation", kind: "data", mode: "analysis", model: "m", busy: false } as Conversation;

beforeEach(() => {
  vi.mocked(api.rename).mockReset().mockResolvedValue(untitled);
});

function renderTitle(conversation: Conversation) {
  const client = new QueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <Title conversation={conversation} />
    </QueryClientProvider>,
  );
  return (next: Conversation) =>
    view.rerender(
      <QueryClientProvider client={client}>
        <Title conversation={next} />
      </QueryClientProvider>,
    );
}

it("doesn't send the default title back over the model's", async () => {
  const update = renderTitle(untitled);
  fireEvent.click(screen.getByRole("button", { name: /rename/ }));
  // The model's title arrives while the editor is open.
  update({ ...untitled, title: "Sleep and mood" });
  fireEvent.blur(screen.getByLabelText("Conversation title"));
  await new Promise((resolve) => setTimeout(resolve, 20)); // time enough for a rename to be sent
  expect(api.rename).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /rename/ })).toHaveTextContent("Sleep and mood");
});

it("sends a name the person typed", async () => {
  const update = renderTitle(untitled);
  fireEvent.click(screen.getByRole("button", { name: /rename/ }));
  update({ ...untitled, title: "Sleep and mood" });
  const input = screen.getByLabelText("Conversation title");
  fireEvent.change(input, { target: { value: "  Sleep   pilot " } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.blur(input);
  await waitFor(() => expect(api.rename).toHaveBeenCalledWith("c", "Sleep pilot"));
  expect(api.rename).toHaveBeenCalledTimes(1); // Enter ended the edit; the blur after it didn't
});

it("decides what an edit renames to", () => {
  expect(titleToSend("Sleep pilot", "New conversation", "New conversation")).toBe("Sleep pilot");
  expect(titleToSend("New conversation", "New conversation", "Sleep and mood")).toBeNull();
  expect(titleToSend(" Sleep  and mood ", "Sleep and mood", "Sleep and mood")).toBeNull();
  expect(titleToSend("New conversation", "Sleep and mood", "Sleep and mood")).toBeNull();
  expect(titleToSend("   ", "Sleep and mood", "Sleep and mood")).toBeNull();
});
