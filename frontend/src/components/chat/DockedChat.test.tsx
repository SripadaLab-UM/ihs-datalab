import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { type ChatContext, DockedChat, withContext } from "./DockedChat";

vi.mock("./useConversationEvents", () => ({ useConversationEvents: () => [] }));
vi.mock("@/api/client", () => ({
  api: {
    modes: vi.fn(),
    models: vi.fn(),
    conversations: vi.fn(),
    createConversation: vi.fn(),
    send: vi.fn(),
    stop: vi.fn(),
    files: vi.fn(async () => []),
  },
}));

// jsdom has no layout, so no scrolling.
Element.prototype.scrollIntoView = vi.fn();

const conversation = (id: string, title = "New conversation"): Conversation => ({
  id, kind: "data", mode: "extraction", title, model: "gpt-5.5",
  created_at: "", updated_at: "", rigor_review: false, busy: false,
}); // prettier-ignore

const query: ChatContext = { label: "The query in the SQL editor", text: "SELECT 1 FROM dual", language: "sql" };

let listed: Conversation[] = [];

beforeEach(() => {
  listed = [];
  vi.mocked(api.modes).mockResolvedValue([
    { id: "extraction", label: "Data extraction", kind: "data", description: "Get a clean dataset.", starters: ["Which tables hold PHQ-9?"] },
  ] as never);
  vi.mocked(api.models).mockResolvedValue({ default: "gpt-5.5", available: ["gpt-5.5"] } as never);
  vi.mocked(api.conversations).mockImplementation(async () => listed);
  vi.mocked(api.createConversation).mockReset().mockImplementation(async () => {
    listed = [conversation("new1")];
    return listed[0];
  });
  vi.mocked(api.send).mockReset().mockImplementation(async (id) => conversation(id));
});

function show(props: Partial<Parameters<typeof DockedChat>[0]> = {}) {
  const client = new QueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <DockedChat mode="extraction" {...props} />
    </QueryClientProvider>,
  );
  return (next: Partial<Parameters<typeof DockedChat>[0]>) =>
    view.rerender(
      <QueryClientProvider client={client}>
        <DockedChat mode="extraction" {...props} {...next} />
      </QueryClientProvider>,
    );
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Your question or instruction"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
}

it("starts a conversation of its mode with the first message, and then shows it", async () => {
  const onConversation = vi.fn();
  show({ onConversation, model: "gpt-5.5" });
  expect(await screen.findByText(/Data session/)).toBeTruthy(); // which session it will be, before anything is sent
  expect(screen.getByText("Data extraction")).toBeTruthy();
  expect(api.createConversation).not.toHaveBeenCalled();
  ask("Which tables hold sleep?");
  await waitFor(() => expect(onConversation).toHaveBeenCalledWith(expect.objectContaining({ id: "new1" })));
  expect(api.createConversation).toHaveBeenCalledWith("extraction", "gpt-5.5");
  expect(api.send).toHaveBeenCalledWith("new1", "Which tables hold sleep?", "medium");
  // Now the conversation itself, which can be renamed.
  expect(await screen.findByRole("button", { name: /rename/ })).toBeTruthy();
});

it("starts one from a starter too", async () => {
  show();
  fireEvent.click(await screen.findByText("Which tables hold PHQ-9?"));
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("new1", "Which tables hold PHQ-9?", "medium"));
});

it("tries a failed first send again in the same conversation", async () => {
  vi.mocked(api.send).mockRejectedValueOnce(new Error("U-M GPT didn't answer."));
  const onConversation = vi.fn();
  show({ onConversation });
  await screen.findByText(/Data session/);
  ask("Which tables hold sleep?");
  expect(await screen.findByText("U-M GPT didn't answer.")).toBeTruthy();
  expect(screen.getByLabelText("Your question or instruction")).toHaveValue("Which tables hold sleep?"); // kept to try again
  expect(onConversation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(onConversation).toHaveBeenCalled());
  expect(api.createConversation).toHaveBeenCalledTimes(1);
  expect(api.send).toHaveBeenLastCalledWith("new1", "Which tables hold sleep?", "medium");
});

it("sends its context with each message, unless the person unticks it", async () => {
  listed = [conversation("c1", "Sleep tables")];
  show({ conversationId: "c1", context: query });
  expect(await screen.findByRole("button", { name: /Sleep tables/ })).toBeTruthy();
  const include = screen.getByRole("checkbox", { name: /Send with your message: The query in the SQL editor/ });
  expect(include).toBeChecked();
  ask("Why is this slow?");
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", withContext("Why is this slow?", query), "medium"));
  fireEvent.click(include);
  ask("Thanks");
  await waitFor(() => expect(api.send).toHaveBeenLastCalledWith("c1", "Thanks", "medium"));
});

it("sends the latest context, and offers none when it's empty", async () => {
  listed = [conversation("c1", "Sleep tables")];
  const update = show({ conversationId: "c1", context: query });
  await screen.findByRole("button", { name: /Sleep tables/ });
  update({ context: { ...query, text: "SELECT 2 FROM dual" } });
  ask("And now?");
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", expect.stringContaining("SELECT 2 FROM dual"), "medium"));
  update({ context: { ...query, text: "  " } });
  expect(screen.queryByRole("checkbox", { name: /Send with your message/ })).toBeNull();
});

it("shows an existing conversation as the Workspace does, with nothing added to messages", async () => {
  listed = [conversation("c1", "Sleep tables")];
  show({ conversationId: "c1" });
  await screen.findByRole("button", { name: /Sleep tables/ });
  expect(screen.queryByRole("checkbox", { name: /Send with your message/ })).toBeNull();
  ask("Why is this slow?");
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", "Why is this slow?", "medium"));
  expect(api.createConversation).not.toHaveBeenCalled();
});

it("says so when its conversation is gone", async () => {
  show({ conversationId: "deleted" });
  expect(await screen.findByText("This conversation isn't in DataLab any more.")).toBeTruthy();
});

it("fences the context after the message", () => {
  expect(withContext("Why?", query)).toBe("Why?\n\nThe query in the SQL editor:\n```sql\nSELECT 1 FROM dual\n```");
  expect(withContext("Why?", undefined)).toBe("Why?");
  expect(withContext("Why?", { label: "Notes", text: " \n" })).toBe("Why?");
  // Backticks in the context can't close its fence early.
  expect(withContext("Why?", { label: "The page", text: "Use ```sql blocks```\n\n" })).toBe(
    "Why?\n\nThe page:\n````\nUse ```sql blocks```\n````",
  );
});
