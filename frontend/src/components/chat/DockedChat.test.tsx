import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

let client: QueryClient;

function show(props: Partial<Parameters<typeof DockedChat>[0]> = {}) {
  client = new QueryClient();
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

const include = () => screen.getByRole("checkbox", { name: /Send with your message: The query in the SQL editor/ });

/** A send that waits until `finish` is called. */
function held<T>(value: T) {
  let finish!: () => void;
  const promise = new Promise<T>((resolve) => (finish = () => resolve(value)));
  return { promise, finish };
}

it("sends its context only once the person ticks it, and then with each message", async () => {
  listed = [conversation("c1", "Sleep tables")];
  show({ conversationId: "c1", context: query });
  expect(await screen.findByRole("button", { name: /Sleep tables/ })).toBeTruthy();
  expect(include()).not.toBeChecked();
  ask("Hello");
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", "Hello", "medium"));
  fireEvent.click(include());
  ask("Why is this slow?");
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("c1", withContext("Why is this slow?", query), "medium"));
  fireEvent.click(include());
  ask("Thanks");
  await waitFor(() => expect(api.send).toHaveBeenLastCalledWith("c1", "Thanks", "medium"));
});

it("sends the latest context, and offers none when it's empty", async () => {
  listed = [conversation("c1", "Sleep tables")];
  const update = show({ conversationId: "c1", context: query });
  await screen.findByRole("button", { name: /Sleep tables/ });
  fireEvent.click(include());
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

it("unticks the context when it's about something else, but not when it's edited", async () => {
  listed = [conversation("c1", "Sleep tables")];
  const update = show({ conversationId: "c1", context: query });
  await screen.findByRole("button", { name: /Sleep tables/ });
  fireEvent.click(include());
  // The same query, edited: still ticked.
  update({ context: { ...query, text: "SELECT 2 FROM dual" } });
  expect(include()).toBeChecked();
  // Another file altogether: the person ticks it again if they want it sent.
  update({ context: { label: "ihsDataR/R/steps.R, as on main", text: "x <- 1", language: "r" } });
  expect(screen.getByRole("checkbox", { name: /Send with your message/ })).not.toBeChecked();
});

it("shows exactly what would be sent, the first lines and then all of them", async () => {
  const long = { ...query, text: "SELECT a,\n  b,\n  c,\n  d\nFROM t\n" };
  show({ context: long });
  const preview = await screen.findByLabelText("What would be sent: The query in the SQL editor");
  expect(screen.getByText("5 lines")).toBeTruthy();
  expect(preview.textContent).toBe("SELECT a,\n  b,\n  c,\n…");
  fireEvent.click(screen.getByRole("button", { name: "Show all 5 lines" }));
  expect(preview.textContent).toBe("SELECT a,\n  b,\n  c,\n  d\nFROM t");
  expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
});

it("sends what was shown as Send was pressed, and holds the choice still while it goes", async () => {
  const making = held(conversation("new1"));
  vi.mocked(api.createConversation).mockReturnValueOnce(making.promise);
  const update = show({ context: query });
  await screen.findByText(/Data session/);
  fireEvent.click(include());
  ask("Why is this slow?");
  // While the conversation is being made, the box can't be unticked, and a new query isn't what goes.
  await waitFor(() => expect(include()).toBeDisabled());
  fireEvent.click(include());
  update({ context: { ...query, text: "SELECT 2 FROM dual" } });
  listed = [conversation("new1")];
  await act(async () => making.finish());
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("new1", withContext("Why is this slow?", query), "medium"));
});

it("starts one conversation for a double click", async () => {
  const making = held(conversation("new1"));
  vi.mocked(api.createConversation).mockReturnValueOnce(making.promise);
  show();
  await screen.findByText(/Data session/);
  fireEvent.change(screen.getByLabelText("Your question or instruction"), { target: { value: "Which tables?" } });
  const button = screen.getByRole("button", { name: /Send/ });
  // Both before the page can render that the first is on its way.
  act(() => {
    fireEvent.click(button);
    fireEvent.click(button);
  });
  listed = [conversation("new1")];
  await act(async () => making.finish());
  await waitFor(() => expect(api.send).toHaveBeenCalled());
  expect(api.createConversation).toHaveBeenCalledTimes(1);
  expect(api.send).toHaveBeenCalledTimes(1);
});

it("lists a conversation it made even when the first message fails", async () => {
  vi.mocked(api.send).mockRejectedValueOnce(new Error("U-M GPT didn't answer."));
  show();
  await screen.findByText(/Data session/);
  const refresh = vi.spyOn(client, "invalidateQueries");
  ask("Which tables hold sleep?");
  expect(await screen.findByText("U-M GPT didn't answer.")).toBeTruthy();
  expect(refresh).toHaveBeenCalledWith({ queryKey: ["conversations"] });
});

it("keeps the cursor in the message box once the conversation starts", async () => {
  show();
  await screen.findByText(/Data session/);
  ask("Which tables hold sleep?");
  await screen.findByRole("button", { name: /rename/ });
  expect(document.activeElement).toBe(screen.getByLabelText("Your question or instruction"));
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
