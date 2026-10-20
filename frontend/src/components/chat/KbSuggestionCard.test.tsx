import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Mode } from "@/api/client";
import { type KbEdit, knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";

import { CompactContext } from "./assistants";
import { KbSuggestionCard, RememberButton } from "./KbSuggestionCard";
import { ShowQueryContext } from "./provenance";
import { buildTranscript, type KbSuggestionItem } from "./transcript";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: {
    status: vi.fn(), pages: vi.fn(), getEdit: vi.fn(), acceptSuggestion: vi.fn(), dismissSuggestion: vi.fn(),
  },
})); // prettier-ignore
vi.mock("@/api/client", () => ({ api: { modes: vi.fn() } }));

const status = (available = true): KnowledgeStatus => ({
  available, repo: available ? "in sync" : "not configured", name: "SripadaLab-UM/ihs-knowledge", signed_in: available,
  account: null, head: "abc1234", last_sync: null, last_error: null, ahead: 0, behind: 0, message: null,
}); // prettier-ignore
const EVENTS = [
  { seq: 1, type: "user_message", data: { text: "Why are some days zero?" } },
  {
    seq: 2, type: "kb_suggestion",
    data: {
      id: "ks_1", by: "agent", turn: 1, page: "sources/fitbit.md", title: "Zero-step days",
      text: "Treat **0 steps** with heart-rate data as missing.", reason: "Analysts read 0 as sedentary.",
      evidence: [{ query_id: "q_1", tables: ["IHS_2025.VFITBITDAILYDATA"] }],
    },
  },
]; // prettier-ignore
const suggestion = (): KbSuggestionItem => buildTranscript(EVENTS)[0].items.find((i) => i.kind === "kb_suggestion") as KbSuggestionItem;
const edit = (more: Partial<KbEdit> = {}): KbEdit => ({
  id: "ke_9", path: "sources/fitbit.md", base: "abc1234", new_page: false, text: "…", text_sha256: "s", status: "draft",
  created_at: "", updated_at: "", origin: null, result: null, commit: null, decided_by: null, before: "…", head: "abc1234",
  upstream_changed: false, theirs: null, theirs_state: "text", ...more,
}); // prettier-ignore
const mode = (id: string, remember: boolean): Mode => ({
  id, label: id, kind: "data", description: "", starters: [], tab_only: false, queries: true, attachments: true, question: "", remember,
}); // prettier-ignore

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}
function showWithRerender(item: KbSuggestionItem) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (value: KbSuggestionItem) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <KbSuggestionCard suggestion={value} conversationId="c_1" />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const view = render(tree(item));
  return { rerender: (value: KbSuggestionItem) => view.rerender(tree(value)) };
}
function show(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const openQuery = vi.fn();
  render(
    <MemoryRouter initialEntries={["/workspace/c_1"]}>
      <QueryClientProvider client={client}>
        <ShowQueryContext value={openQuery}>
          <Routes>
            <Route path="*" element={<>{node}<Where /></>} />
          </Routes>
        </ShowQueryContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { openQuery };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(knowledgeApi.status).mockReset().mockResolvedValue(status());
  vi.mocked(knowledgeApi.pages).mockReset().mockResolvedValue({ head: "abc1234", pages: [] });
  vi.mocked(knowledgeApi.getEdit).mockReset().mockResolvedValue(edit());
  vi.mocked(knowledgeApi.acceptSuggestion).mockReset().mockResolvedValue(edit());
  vi.mocked(knowledgeApi.dismissSuggestion).mockReset().mockResolvedValue({ id: "ks_1", status: "dismissed", page: "sources/fitbit.md" });
  vi.mocked(api.modes).mockReset().mockResolvedValue([mode("analysis", true), mode("sql", false)]);
});

it("reads the suggestion from the conversation's events", () => {
  const item = suggestion();
  expect(item).toMatchObject({ id: "ks_1", page: "sources/fitbit.md", status: "open", evidence: [{ query_id: "q_1" }] });
  const later = buildTranscript([...EVENTS, { seq: 3, type: "kb_suggestion_updated", data: { id: "ks_1", status: "accepted", edit_id: "ke_9" } }]);
  expect(later[0].items.find((i) => i.kind === "kb_suggestion")).toMatchObject({ status: "accepted", editId: "ke_9" });
});

const toggle = () => screen.getByRole("button", { expanded: false, name: /Suggested Knowledge update/ });
const opener = () => screen.getByRole("button", { name: /Suggested Knowledge update/ });

it("starts folded to a row: what it is, its title and page, pending, and Accept", async () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  const row = opener();
  expect(row).toHaveAttribute("aria-expanded", "false");
  expect(row).toHaveTextContent("Zero-step days");
  expect(row).toHaveTextContent("sources/fitbit.md");
  expect(row).toHaveTextContent("pending");
  expect(document.getElementById(row.getAttribute("aria-controls")!)).toBeEmptyDOMElement();
  expect(screen.queryByText("Analysts read 0 as sedentary.")).toBeNull();
  expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  expect(await screen.findByRole("button", { name: "Accept as proposal" })).toBeTruthy();
});

it("opens onto the page, the text, why, and the queries behind it", async () => {
  const { openQuery } = show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  fireEvent.click(toggle());
  expect(opener()).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Suggested Knowledge update")).toBeTruthy();
  expect(screen.getByText("0 steps").tagName).toBe("STRONG"); // Markdown
  expect(screen.getByText("Analysts read 0 as sedentary.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /VFITBITDAILYDATA/ }));
  expect(openQuery).toHaveBeenCalledWith("q_1");
  expect(await screen.findByText("a new page (a draft)")).toBeTruthy();
  // One Accept, with Dismiss and Edit first, while it's open.
  expect(screen.getAllByRole("button", { name: "Accept as proposal" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Dismiss" })).toBeTruthy();
  fireEvent.click(opener());
  expect(opener()).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Analysts read 0 as sedentary.")).toBeNull();
});

it("remembers it's open for the session", () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  fireEvent.click(toggle());
  cleanup();
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  expect(opener()).toHaveAttribute("aria-expanded", "true");
});

it("Accept as proposal, from the folded row, makes it a draft edit and says so on the row", async () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  const accept = await screen.findByRole("button", { name: "Accept as proposal" });
  await waitFor(() => expect(accept).toBeEnabled());
  fireEvent.click(accept);
  const link = await screen.findByRole("link", { name: "Review in Knowledge →" });
  expect(link.getAttribute("href")).toBe("/knowledge/sources/fitbit.md?edit=ke_9&view=review");
  expect(document.activeElement).toBe(link);
  expect(knowledgeApi.acceptSuggestion).toHaveBeenCalledWith("c_1", "ks_1");
  expect(opener()).toHaveTextContent("accepted as a draft");
  expect(opener()).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByTestId("where").textContent).toBe("/workspace/c_1");
  // It can be opened again.
  fireEvent.click(opener());
  expect(screen.getByText(/on this computer: not shared yet/)).toBeTruthy();
  expect(screen.getByText("Analysts read 0 as sedentary.")).toBeTruthy();
});

it("Accept from the open card folds it back to the row", async () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  fireEvent.click(toggle());
  const accept = screen.getByRole("button", { name: "Accept as proposal" });
  await waitFor(() => expect(accept).toBeEnabled());
  fireEvent.click(accept);
  const link = await screen.findByRole("link", { name: "Review in Knowledge →" });
  expect(opener()).toHaveAttribute("aria-expanded", "false");
  expect(document.activeElement).toBe(link);
  expect(screen.queryByText("Analysts read 0 as sedentary.")).toBeNull();
});

it("Edit first opens the editor on it at once", async () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  fireEvent.click(toggle());
  const first = await screen.findByRole("button", { name: /Edit first/ });
  await waitFor(() => expect(first).toBeEnabled());
  fireEvent.click(first);
  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/knowledge/sources/fitbit.md?edit=ke_9&view=edit"));
});

it("Dismiss sets it aside and folds it to the row", async () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  fireEvent.click(toggle());
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(opener()).toHaveTextContent("dismissed"));
  expect(knowledgeApi.dismissSuggestion).toHaveBeenCalledWith("c_1", "ks_1");
  expect(opener()).toHaveAttribute("aria-expanded", "false");
  expect(document.activeElement).toBe(opener());
  expect(screen.queryByRole("button", { name: "Accept as proposal" })).toBeNull();
  fireEvent.click(opener());
  expect(screen.getByText(/Dismissed: it wasn't added/)).toBeTruthy();
});

it("folds when it's accepted elsewhere, without moving focus", async () => {
  const { rerender } = showWithRerender(suggestion());
  fireEvent.click(toggle());
  const other = document.createElement("button");
  document.body.append(other);
  other.focus();
  rerender({ ...suggestion(), status: "accepted", editId: "ke_9" });
  expect(await screen.findByRole("link", { name: "Review in Knowledge →" })).toBeTruthy();
  expect(opener()).toHaveAttribute("aria-expanded", "false");
  expect(document.activeElement).toBe(other);
  other.remove();
});

it("once shared, says so with the commit", async () => {
  vi.mocked(knowledgeApi.getEdit).mockResolvedValue(edit({ status: "saved", commit: "c0ffee1234" }));
  show(<KbSuggestionCard suggestion={{ ...suggestion(), status: "accepted", editId: "ke_9" }} conversationId="c_1" />);
  expect(await screen.findByText(/Shared to GitHub/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "c0ffee1" })).toBeTruthy();
  expect(opener()).toHaveTextContent("shared");
});

it("docked beside a tab, the row is tighter and the page's note waits for it to open", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <CompactContext value>
          <KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />
        </CompactContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const row = opener();
  expect(row.className).toContain("py-1.5");
  expect(await screen.findByRole("button", { name: "Accept as proposal" })).toBeTruthy();
  await waitFor(() => expect(knowledgeApi.pages).toHaveBeenCalled());
  expect(row).not.toHaveTextContent("a new page (a draft)");
  fireEvent.click(row);
  expect(await screen.findByText("a new page (a draft)")).toBeTruthy();
});

it("works from the keyboard: the row is a native button that says whether it's open and what it opens", () => {
  show(<KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />);
  const row = opener();
  // A native button: Enter and Space press it, and Tab reaches it.
  expect(row.tagName).toBe("BUTTON");
  expect(row).toHaveAttribute("type", "button");
  row.focus();
  expect(document.activeElement).toBe(row);
  fireEvent.click(row);
  const region = document.getElementById(row.getAttribute("aria-controls")!)!;
  expect(region).toHaveTextContent("Analysts read 0 as sedentary.");
  expect(document.activeElement).toBe(row);
});

it("on practice DataLab, the actions say they're for the real DataLab", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue(status(false));
  show(
    <>
      <KbSuggestionCard suggestion={suggestion()} conversationId="c_1" />
      <RememberButton mode="analysis" onSend={vi.fn()} busy={false} />
    </>,
  );
  // On the folded row too.
  expect(await screen.findByText("Available on the real DataLab")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Accept as proposal" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Accept as proposal" }).title).toBe("Available on the real DataLab");
  fireEvent.click(toggle());
  expect(screen.getByText(/Available on the real DataLab: practice DataLab has no lab knowledge base/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Accept as proposal" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Edit first/ })).toBeDisabled();
  const remember = await screen.findByRole("button", { name: /Remember in Knowledge/ });
  expect(remember).toBeDisabled();
  expect(remember.title).toContain("Available on the real DataLab");
});

const REMEMBER_EVENTS = [
  { seq: 1, type: "user_message", data: { text: "Zero steps means not synced.", kb_request: true } },
  {
    seq: 2, type: "kb_suggestion",
    data: {
      id: "ks_2", by: "agent", requested: true, turn: 1, page: "qc/zero-steps.md", title: "Zero-step days",
      text: "Treat 0 steps as not synced.", reason: "The person asked to keep it.", evidence: [],
    },
  },
  { seq: 3, type: "kb_suggestion_updated", data: { id: "ks_2", status: "accepted", edit_id: "ke_9" } },
]; // prettier-ignore

it("Remember in Knowledge: one box, sent to the agent as a marked message", async () => {
  const onSend = vi.fn().mockResolvedValue(undefined);
  show(<RememberButton mode="analysis" onSend={onSend} busy={false} />);
  const open = await screen.findByRole("button", { name: /Remember in Knowledge/ });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  await screen.findByRole("dialog", { name: "Remember in Knowledge" });
  const ask = screen.getByRole("button", { name: "Ask the agent" });
  expect(ask).toBeDisabled();
  fireEvent.change(screen.getByLabelText("What should DataLab remember?"), {
    target: { value: "  A step count of 0 means the tracker wasn't synced.  " },
  });
  fireEvent.click(ask);
  await waitFor(() => expect(onSend).toHaveBeenCalledWith("A step count of 0 means the tracker wasn't synced."));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("Remember in Knowledge keeps the text and says why when it couldn't be sent", async () => {
  const onSend = vi.fn().mockRejectedValue(new Error("The agent is still working on the previous message."));
  show(<RememberButton mode="analysis" onSend={onSend} busy={false} />);
  const open = await screen.findByRole("button", { name: /Remember in Knowledge/ });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  const box = await screen.findByLabelText("What should DataLab remember?");
  fireEvent.change(box, { target: { value: "Keep this." } });
  fireEvent.click(screen.getByRole("button", { name: "Ask the agent" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("still working");
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(box).toHaveValue("Keep this.");
});

it("Remember in Knowledge is offered only where the agent can suggest updates, and waits while it works", async () => {
  show(
    <>
      <RememberButton mode="sql" onSend={vi.fn()} busy={false} />
      <RememberButton mode="analysis" onSend={vi.fn()} busy />
    </>,
  );
  const [only] = await screen.findAllByRole("button", { name: /Remember in Knowledge/ });
  expect(screen.getAllByRole("button", { name: /Remember in Knowledge/ })).toHaveLength(1);
  await waitFor(() => expect(only.title).toContain("The agent is working"));
  expect(only).toBeDisabled();
});

it("a requested update reads as the person's, with no query needed as evidence", async () => {
  const turn = buildTranscript(REMEMBER_EVENTS)[0];
  expect(turn.kbRequest).toBe(true);
  const item = turn.items.find((i) => i.kind === "kb_suggestion") as KbSuggestionItem;
  expect(item).toMatchObject({ requested: true, status: "accepted", editId: "ke_9", evidence: [] });
  show(<KbSuggestionCard suggestion={item} conversationId="c_1" />);
  expect(screen.getByRole("region", { name: "Your Knowledge update: Zero-step days" })).toBeTruthy();
  expect(await screen.findByRole("link", { name: "Review in Knowledge →" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { expanded: false, name: /Your Knowledge update/ }));
  expect(screen.getByText("None from this conversation: you asked to keep it")).toBeTruthy();
});

