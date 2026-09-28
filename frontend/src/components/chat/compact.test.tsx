// The chats docked beside a tab (SQL Playground, Workflows, Pipelines, Knowledge):
// their compact intro, a message box that fits the panel, and a draft that
// outlives hiding the chat. The Workspace keeps its full presentation.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { ASSISTANTS, type AssistantId, draftKeyOf } from "./assistants";
import { latestRows } from "./Chat";
import { type ChatContext, DockedChat, withContext } from "./DockedChat";
import type { Row } from "./activity";

const stream = vi.hoisted(() => ({ events: [] as { seq: number; type: string; data: Record<string, unknown> }[] }));
vi.mock("./useConversationEvents", () => ({ useConversationEvents: () => stream.events }));
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

const MODES = [
  { id: "sql", label: "SQL drafting", kind: "data", description: "Describe the data you want.", starters: ["Daily Fitbit steps for March.", "Nightly sleep minutes."], tab_only: true, queries: true, question: "What would you like to find out?" },
  { id: "workflows", label: "Workflow authoring", kind: "data", description: "Draft a workflow.", starters: ["Check the workflow files for missing QC."], tab_only: true, queries: true, question: "What should this workflow do?" },
  { id: "pipelines", label: "Pipelines", kind: "data", description: "Explain, edit and test.", starters: ["Run the package's tests."], tab_only: true, queries: true, question: "What would you like to find out?" },
  { id: "knowledge", label: "Knowledge writing", kind: "data", description: "Write or tidy a page.", starters: ["Draft a table page."], tab_only: true, queries: false, question: "What would you like to find out?" },
  { id: "analysis", label: "Analysis", kind: "data", description: "Answer a scientific question.", starters: ["How did steps change?"], tab_only: false, queries: true, question: "What would you like to find out?" },
]; // prettier-ignore

const conversation = (id: string, mode = "sql"): Conversation => ({
  id, kind: "data", mode, title: "Steps in March", model: "gpt-5.5",
  created_at: "", updated_at: "", rigor_review: false, busy: false,
}); // prettier-ignore

let listed: Conversation[] = [];

beforeEach(() => {
  sessionStorage.clear();
  stream.events = [];
  listed = [];
  vi.mocked(api.modes).mockResolvedValue(MODES as never);
  vi.mocked(api.models).mockResolvedValue({ default: "gpt-5.5", available: ["gpt-5.5"] } as never);
  vi.mocked(api.conversations).mockImplementation(async () => listed);
  vi.mocked(api.createConversation).mockReset().mockImplementation(async (mode) => {
    listed = [conversation("new1", mode)];
    return listed[0];
  });
  vi.mocked(api.send).mockReset().mockImplementation(async (id) => conversation(id));
});

function show(props: Partial<Parameters<typeof DockedChat>[0]> & { mode: string }) {
  const client = new QueryClient();
  const tree = (next: Partial<Parameters<typeof DockedChat>[0]> = {}) => (
    <QueryClientProvider client={client}>
      <DockedChat {...props} {...next} />
    </QueryClientProvider>
  );
  const view = render(tree());
  return { ...view, again: (next: Partial<Parameters<typeof DockedChat>[0]> = {}) => view.rerender(tree(next)) };
}

const box = () => screen.getByLabelText("Your question or instruction") as HTMLTextAreaElement;

it.each(["sql", "workflows", "pipelines", "knowledge"] as AssistantId[])(
  "gives the %s tab's chat a compact intro: a title, one sentence, its starters, and what it can do folded",
  async (id) => {
    show({ mode: id, assistant: id });
    const copy = ASSISTANTS[id];
    expect(await screen.findByRole("heading", { name: copy.title })).toBeInTheDocument();
    const intro = await screen.findByTestId("compact-intro");
    expect(intro).toHaveTextContent(copy.description);
    // One or two short starters, sent as they are.
    const starters = within(intro).getAllByRole("listitem");
    expect(starters.length).toBeGreaterThanOrEqual(1);
    expect(starters.length).toBeLessThanOrEqual(2);
    // No big question, no three columns.
    expect(screen.queryByText(/What would you like to find out\?|What should this workflow do\?/)).toBeNull();
    expect(document.querySelector(".sm\\:grid-cols-3")).toBeNull();
    expect(document.querySelector(".text-\\[42px\\]")).toBeNull();
    // What it can do: one line, closed, and then this tab's own work and the session's limits.
    const more = within(intro).getByRole("button", { name: "What it can do" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(copy.can[0][1])).toBeNull();
    fireEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(copy.can[0][1])).toBeInTheDocument();
    expect(screen.getByText("Websites blocked; the model is U-M's approved GPT service")).toBeInTheDocument();
    expect(
      screen.getByText(
        id === "knowledge" ? "Reads the database catalog (tables and columns), never rows" : "Queries the IHS database, read-only",
      ),
    ).toBeInTheDocument();
    // Its own short wording in the box, and which session it is.
    expect(box()).toHaveAttribute("placeholder", copy.placeholder);
    expect(screen.getByText("Data session")).toBeInTheDocument();
  },
);

it("leaves the Workspace's chat as it was: the mode's heading, the question, three columns and its starters", async () => {
  show({ mode: "analysis" });
  expect(await screen.findByText("What would you like to find out?")).toBeInTheDocument();
  expect(screen.getByText("Analysis")).toBeInTheDocument();
  expect(screen.getByText("Try one of these, or ask your own")).toBeInTheDocument();
  expect(document.querySelector(".sm\\:grid-cols-3")?.children).toHaveLength(3);
  expect(screen.queryByTestId("compact-intro")).toBeNull();
  expect(screen.getByText(/Data session · database access, web blocked/)).toBeInTheDocument();
  expect(box()).toHaveAttribute("placeholder", "Ask a question, or say what to do next");
  expect(screen.getByRole("button", { name: /Send/ })).toHaveTextContent("Send");
  expect(screen.getByText(/the agent shows its steps as it works/)).toBeInTheDocument();
});

it("keeps the box and its button whole in a narrow panel: the label gives way to the icon, not the box", async () => {
  show({ mode: "sql", assistant: "sql", sendLabel: "Generate SQL" });
  await screen.findByTestId("compact-intro");
  const footer = box().closest("footer")!;
  // Sized by the panel it's in, not the window.
  expect(footer).toHaveClass("@container", "shrink-0");
  const row = screen.getByTestId("composer-box");
  expect(row).toHaveClass("flex", "min-w-0", "items-end");
  expect(box()).toHaveClass("min-w-0", "flex-1", "placeholder:truncate");
  const send = within(row).getByRole("button", { name: "Generate SQL" });
  expect(send).toHaveClass("shrink-0");
  // The words show only where the panel has room; the name stays for screen readers either way.
  const words = within(send).getByText("Generate SQL");
  expect(words).toHaveClass("hidden", "@[20rem]:inline");
  // Every tab's wording is short enough to sit on one line beside the button.
  for (const copy of Object.values(ASSISTANTS)) expect(copy.placeholder.length).toBeLessThanOrEqual(32);
});

it("grows with what's typed, up to about six lines, and then scrolls within itself", async () => {
  // jsdom has no layout: each line of text is 23px high.
  const scroll = vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLTextAreaElement) {
    return 12 + 23 * this.value.split("\n").length;
  });
  show({ mode: "sql", assistant: "sql" });
  await screen.findByTestId("compact-intro");
  fireEvent.change(box(), { target: { value: "one\ntwo\nthree" } });
  expect(box().style.height).toBe("81px");
  expect(box().style.overflowY).toBe("hidden");
  fireEvent.change(box(), { target: { value: Array.from({ length: 12 }, (_, i) => `line ${i}`).join("\n") } });
  expect(box().style.height).toBe("150px");
  expect(box().style.overflowY).toBe("auto");
  // It stays in the footer, under the messages: it never lies over them.
  expect(box().closest("footer")!.previousElementSibling).toHaveClass("min-h-0", "flex-1", "overflow-y-auto");
  scroll.mockRestore();
});

it("keeps the draft, per tab, when the chat is hidden and opened again or the tab is left", async () => {
  const sql = show({ mode: "sql", assistant: "sql" });
  await screen.findByTestId("compact-intro");
  fireEvent.change(box(), { target: { value: "Daily steps, but only for April" } });
  // Hidden (or the tab left): the chat goes from the page.
  sql.unmount();
  expect(sessionStorage.getItem(draftKeyOf("sql"))).toBe("Daily steps, but only for April");
  // Another tab's chat has its own.
  const knowledge = show({ mode: "knowledge", assistant: "knowledge" });
  await screen.findByTestId("compact-intro");
  expect(box()).toHaveValue("");
  knowledge.unmount();
  // Opened again: the draft is back.
  show({ mode: "sql", assistant: "sql" });
  await screen.findByTestId("compact-intro");
  expect(box()).toHaveValue("Daily steps, but only for April");
  // Sent, it's gone from the box and from the tab's storage.
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("new1", "Daily steps, but only for April", "medium"));
  expect(sessionStorage.getItem(draftKeyOf("sql"))).toBeNull();
});

it("keeps the draft in a conversation that has started, too", async () => {
  listed = [conversation("c1")];
  sessionStorage.setItem(draftKeyOf("sql", "c1"), "and include Garmin");
  const first = show({ mode: "sql", assistant: "sql", conversationId: "c1" });
  expect(await screen.findByRole("button", { name: /Steps in March/ })).toBeInTheDocument();
  expect(box()).toHaveValue("and include Garmin");
  fireEvent.change(box(), { target: { value: "and include Garmin, per week" } });
  first.unmount();
  show({ mode: "sql", assistant: "sql", conversationId: "c1" });
  await screen.findByRole("button", { name: /Steps in March/ });
  expect(box()).toHaveValue("and include Garmin, per week");
  expect(screen.getByRole("heading", { name: "SQL assistant" })).toBeInTheDocument();
});

it("starts New chat with an empty box: the draft belongs to its conversation", async () => {
  listed = [conversation("c1")];
  const first = show({ mode: "sql", assistant: "sql", conversationId: "c1" });
  await screen.findByRole("button", { name: /Steps in March/ });
  fireEvent.change(box(), { target: { value: "and per week" } });
  first.unmount();
  // New chat: the page forgets the conversation and remounts the chat.
  const fresh = show({ mode: "sql", assistant: "sql" });
  await screen.findByTestId("compact-intro");
  expect(box()).toHaveValue("");
  fresh.unmount();
  // Back to the first conversation: its draft is there.
  show({ mode: "sql", assistant: "sql", conversationId: "c1" });
  await screen.findByRole("button", { name: /Steps in March/ });
  expect(box()).toHaveValue("and per week");
});

it("says so when an older conversation of another mode opens in the tab's chat", async () => {
  vi.mocked(api.modes).mockResolvedValue([...MODES, { id: "engineering", label: "Data engineering", kind: "data", description: "", starters: [], tab_only: false, queries: true }] as never); // prettier-ignore
  listed = [{ ...conversation("c1", "engineering"), title: "Old tests chat" }];
  const view = show({ mode: "pipelines", assistant: "pipelines", conversationId: "c1" });
  await screen.findByRole("button", { name: /Old tests chat/ });
  expect(await screen.findByText("Data engineering")).toBeInTheDocument();
  view.unmount();
  // Its own mode: no label.
  listed = [conversation("c2", "pipelines")];
  show({ mode: "pipelines", assistant: "pipelines", conversationId: "c2" });
  await screen.findByRole("button", { name: /Steps in March/ });
  expect(screen.queryByText("Data engineering")).toBeNull();
});

const page: ChatContext = {
  label: "The page open in the Knowledge tab (sources/fitbit.md)",
  name: "sources/fitbit.md",
  text: "# Fitbit\n\nWear time is…\n",
  language: "markdown",
};

it("shows the open page as one row, not sent until ticked, and sends it once it is", async () => {
  show({ mode: "knowledge", assistant: "knowledge", context: page });
  const row = await screen.findByTestId("context-row");
  const tick = within(row).getByRole("checkbox", { name: /Send with message/ });
  expect(tick).not.toBeChecked();
  expect(row).toHaveTextContent("sources/fitbit.md");
  expect(row).toHaveTextContent("Not sent.");
  // Nothing of the page shows until asked for.
  expect(screen.queryByText(/Wear time is/)).toBeNull();
  // A starter goes without it, as the row says.
  fireEvent.click(await screen.findByText("Draft a table page."));
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("new1", "Draft a table page.", "medium"));
});

it("sends the open page with the message once ticked", async () => {
  show({ mode: "knowledge", assistant: "knowledge", context: page });
  const row = await screen.findByTestId("context-row");
  fireEvent.click(within(row).getByRole("checkbox", { name: /Send with message/ }));
  expect(row).toHaveTextContent("Goes with each message you send");
  fireEvent.change(box(), { target: { value: "Is the wear-time rule right?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(api.send).toHaveBeenCalledWith("new1", withContext("Is the wear-time rule right?", page), "medium"));
});

it("lets New workflow's form ask the first question: the chat has no box of its own until then", async () => {
  show({ mode: "workflows", assistant: "workflows", handoff: <p>Start with the form on this page.</p> });
  expect(await screen.findByText("Start with the form on this page.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Workflow assistant" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Your question or instruction")).toBeNull();
  expect(screen.queryByTestId("compact-intro")).toBeNull();
});

it("folds a long story in progress to its latest steps, keeping approvals and errors in view", () => {
  const say = (n: number): Row => ({ type: "say", key: `s${n}`, text: `step ${n}` });
  const error: Row = { type: "notice", key: "e", text: "It failed", tone: "error" };
  const rows = [say(1), error, say(2), say(3), say(4), say(5), say(6), say(7)];
  const { shown, folded } = latestRows(rows);
  expect(shown).toEqual([error, say(4), say(5), say(6), say(7)]);
  expect(folded).toBe(3);
  // A short one shows whole.
  expect(latestRows(rows.slice(0, 5))).toEqual({ shown: rows.slice(0, 5), folded: 0 });
});

it("never folds away DataLab's notices or a step that needs a look, however long the story", () => {
  const say = (n: number): Row => ({ type: "say", key: `s${n}`, text: `step ${n}` });
  const notice: Row = { type: "notice", key: "n", text: "Couldn't save a checkpoint: the disk is nearly full.", tone: "info" };
  const join: Row = {
    type: "step",
    step: { key: "j", icon: "link", title: "Looked for a join", tone: "attn", chips: [{ text: "no shared participant ID", tone: "attn" }], detail: null },
  };
  const rows = [say(1), notice, say(2), join, say(3), say(4), say(5), say(6), say(7), say(8)];
  const { shown, folded } = latestRows(rows);
  expect(shown).toEqual([notice, join, say(5), say(6), say(7), say(8)]);
  expect(folded).toBe(4);
});

it("names a failing workflow check, so it isn't folded away as done", async () => {
  const { activityRows } = await import("./activity");
  const tool = (id: string, summary: Record<string, unknown>) => ({
    kind: "tool" as const, id, tool: "check_workflow", server: "ihs-data", status: "completed", arguments: {}, error: null, summary,
  }); // prettier-ignore
  const [bad, good] = activityRows([tool("t1", { valid: false, problem_count: 3 }), tool("t2", { valid: true, problem_count: 0 })], false);
  expect(bad).toMatchObject({ type: "step", step: { tone: "attn", chips: [{ text: "3 problems", tone: "attn" }] } });
  expect(good).toMatchObject({ type: "step", step: { tone: "done", chips: [{ text: "passes", tone: "good" }] } });
  // Folded to its latest rows, the failing check stays.
  const rows = [bad, ...Array.from({ length: 6 }, (_, i): Row => ({ type: "say", key: `s${i}`, text: `${i}` }))];
  expect(latestRows(rows).shown[0]).toBe(bad);
});

it("folds the checks in a docked answer, and says on the fold when the rigor review flagged something", async () => {
  listed = [conversation("c1", "knowledge")];
  stream.events = [
    { seq: 1, type: "user_message", data: { text: "How many interns wore a Fitbit?" } },
    { seq: 2, type: "turn_started", data: {} },
    { seq: 3, type: "answer", data: { text: "About 40 of them." } },
    { seq: 4, type: "trace", data: { numbers: 1, untraced: [] } },
    { seq: 5, type: "review_started", data: {} },
    { seq: 6, type: "review", data: { text: "1. Traced claims: 40 is not in any output." } },
    { seq: 7, type: "review_finished", data: { status: "done" } },
    { seq: 8, type: "turn_finished", data: { status: "completed" } },
    { seq: 9, type: "turn_done", data: {} },
  ];
  await act(async () => {
    show({ mode: "knowledge", assistant: "knowledge", conversationId: "c1" });
  });
  const checks = await screen.findByTestId("answer-checks");
  const fold = within(checks).getByRole("button", { name: /Checks on this answer/ });
  expect(fold).toHaveAttribute("aria-expanded", "false");
  // DataLab's own check passed, but the review didn't: the fold says to look.
  expect(within(fold).getByText("needs a look")).toBeInTheDocument();
  fireEvent.click(fold);
  expect(within(checks).getByText(/all 1|the 1 number matched/)).toBeInTheDocument();
});

it("shows a docked conversation in the panel's own sizes: the question as a message, the checks folded", async () => {
  listed = [conversation("c1", "knowledge")];
  stream.events = [
    { seq: 1, type: "user_message", data: { text: "What does this page claim?" } },
    { seq: 2, type: "turn_started", data: {} },
    { seq: 3, type: "answer", data: { text: "It claims **three** things.\n\n```sql\nSELECT 1 FROM dual\n```" } },
    { seq: 4, type: "turn_finished", data: { status: "completed" } },
    { seq: 5, type: "turn_done", data: {} },
  ];
  await act(async () => {
    show({ mode: "knowledge", assistant: "knowledge", conversationId: "c1" });
  });
  expect(await screen.findByRole("heading", { name: "Knowledge assistant" })).toBeInTheDocument();
  const question = screen.getByText("What does this page claim?");
  expect(question.tagName).toBe("P");
  expect(question).toHaveClass("text-[17px]");
  expect(screen.getByText("SELECT 1 FROM dual")).toBeInTheDocument();
});

it("folds a proposed knowledge edit in a docked chat, and opens it on request", async () => {
  const { ProposalCard } = await import("./ProposalCard");
  const { CompactContext } = await import("./assistants");
  const { MemoryRouter } = await import("react-router");
  const proposal = {
    kind: "kb_proposal" as const, id: "kp_1", status: "open", message: "", commit: null, truncated: false,
    files: [{ path: "sources/fitbit.md", change: "modified", added: 2, removed: 1, flags: [] }],
    refused: [], check: { errors: 0, data: 0, warnings: 0 },
  }; // prettier-ignore
  const card = (compact: boolean) => (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <CompactContext value={compact}>
          <ProposalCard proposal={proposal as never} />
        </CompactContext>
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(card(true));
  // Its files and checks, and one button to open the review.
  expect(screen.getByText("sources/fitbit.md")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review the changes" })).toBeInTheDocument();
  view.unmount();
  // The Workspace opens it at once, as before.
  render(card(false));
  expect(screen.queryByRole("button", { name: "Review the changes" })).toBeNull();
});
