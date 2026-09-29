import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";
import { type KbEdit, knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";

import { CompactContext } from "./assistants";
import { KbSuggestionCard, ProposeUpdateButton } from "./KbSuggestionCard";
import { ShowQueryContext } from "./provenance";
import { buildTranscript, type KbSuggestionItem } from "./transcript";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: {
    status: vi.fn(), pages: vi.fn(), getEdit: vi.fn(), acceptSuggestion: vi.fn(), dismissSuggestion: vi.fn(), proposeUpdate: vi.fn(),
  },
})); // prettier-ignore
vi.mock("@/api/client", () => ({ api: { dataAccessed: vi.fn() } }));

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
const conversation = { id: "c_1", kind: "data", mode: "analysis", title: "t", model: "m", created_at: "", updated_at: "", rigor_review: false, express: false, busy: false } as Conversation;

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
  vi.mocked(knowledgeApi.proposeUpdate).mockReset().mockResolvedValue(edit({ id: "ke_10" }));
  vi.mocked(api.dataAccessed).mockReset().mockResolvedValue([
    { id: "q_1", started_at: "2026-09-28T10:00:00Z", status: "succeeded", sql_text: "SELECT …", tables: ["IHS_2025.VFITBITDAILYDATA"], row_count: 1, elapsed_ms: 5, result_file: null, message: null },
    { id: "q_2", started_at: "2026-09-28T10:01:00Z", status: "failed", sql_text: "SELECT …", tables: ["IHS_2025.X"], row_count: null, elapsed_ms: 5, result_file: null, message: "no" },
  ]); // prettier-ignore
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
      <ProposeUpdateButton conversation={conversation} />
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
  expect(screen.getByRole("button", { name: /Propose a Knowledge update/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Propose a Knowledge update/ }).title).toContain("Available on the real DataLab");
});

it("Propose a Knowledge update: the person's own, with this conversation's queries as evidence", async () => {
  show(<ProposeUpdateButton conversation={conversation} />);
  const open = await screen.findByRole("button", { name: /Propose a Knowledge update/ });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  const dialog = await screen.findByRole("dialog", { name: "Propose a Knowledge update" });
  const create = screen.getByRole("button", { name: "Create the proposal" });
  expect(create).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Page"), { target: { value: "qc/zero-step-days.md" } });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Zero-step days" } });
  fireEvent.change(screen.getByLabelText("What to add (Markdown)"), { target: { value: "Treat 0 as missing." } });
  fireEvent.change(screen.getByLabelText("Why it's worth keeping"), { target: { value: "It keeps coming up." } });
  // Only the queries that ran can be evidence.
  const boxes = await screen.findAllByRole("checkbox");
  expect(boxes).toHaveLength(1);
  expect(dialog.textContent).not.toContain("q_2");
  expect(create).toBeDisabled();
  fireEvent.click(boxes[0]);
  fireEvent.click(create);
  await waitFor(() =>
    expect(knowledgeApi.proposeUpdate).toHaveBeenCalledWith("c_1", {
      page: "qc/zero-step-days.md", title: "Zero-step days", text: "Treat 0 as missing.", reason: "It keeps coming up.", evidence_query_ids: ["q_1"],
    }),
  ); // prettier-ignore
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("shows why DataLab refused a proposal", async () => {
  vi.mocked(knowledgeApi.proposeUpdate).mockRejectedValue(new Error("This may hold participant-level data, so it wasn't suggested."));
  show(<ProposeUpdateButton conversation={conversation} />);
  const open = await screen.findByRole("button", { name: /Propose a Knowledge update/ });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  for (const [label, value] of [["Page", "qc/x.md"], ["Title", "t"], ["What to add (Markdown)", "P12345"], ["Why it's worth keeping", "r"]]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click((await screen.findAllByRole("checkbox"))[0]);
  fireEvent.click(screen.getByRole("button", { name: "Create the proposal" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("participant-level data");
  expect(screen.getByRole("dialog")).toBeTruthy();
});
