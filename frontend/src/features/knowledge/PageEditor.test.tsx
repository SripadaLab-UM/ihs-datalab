import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { type KbEdit, type KbEditCheck, type KbEntry, type KbPage, knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";
import type { ChatContext, ChatRequest } from "@/components/chat/DockedChat";

import { KnowledgePage } from "./KnowledgePage";
import { PageEditor, readUnsaved } from "./PageEditor";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: {
    status: vi.fn(), sync: vi.fn(), pages: vi.fn(), page: vi.fn(), history: vi.fn(), edits: vi.fn(),
    startEdit: vi.fn(), getEdit: vi.fn(), keepEdit: vi.fn(), checkEdit: vi.fn(), reapplyEdit: vi.fn(),
    shareEdit: vi.fn(), discardEdit: vi.fn(),
  },
})); // prettier-ignore
const chat = vi.hoisted(() => ({ props: null as null | { context?: ChatContext; request?: ChatRequest } }));
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: (props: { context?: ChatContext; request?: ChatRequest; headerActions?: ReactNode }) => {
    chat.props = props;
    return <div>docked chat</div>;
  },
}));
// The editors as plain fields: what they show, and typing into them.
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: ({ label, value, onChange, readOnly }: { label: string; value: string; onChange?: (v: string) => void; readOnly?: boolean }) => (
    <textarea aria-label={label} value={value} readOnly={readOnly} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));
vi.mock("@/components/editor/DiffView", () => ({
  DiffView: ({ label, original, modified }: { label: string; original: string; modified: string }) => (
    <div aria-label={label}>
      <pre data-testid="diff-original">{original}</pre>
      <pre data-testid="diff-modified">{modified}</pre>
    </div>
  ),
}));
Element.prototype.scrollIntoView = vi.fn();

const FITBIT = "---\nid: fitbit\nkind: source\nstatus: draft\n---\n\n# Fitbit\n";
const edit = (more: Partial<KbEdit> = {}): KbEdit => ({
  id: "ke_1", path: "sources/fitbit.md", base: "abc1234", new_page: false, text: FITBIT, text_sha256: "sha-kept",
  status: "draft", created_at: "t0", updated_at: "t1", origin: null, result: null, commit: null, decided_by: null,
  before: FITBIT, head: "abc1234", upstream_changed: false, theirs: null, theirs_state: "text", ...more,
}); // prettier-ignore
const checked = (text: string, more: Partial<KbEditCheck> = {}): KbEditCheck => ({
  findings: [], shared: text, diff: "", notes: [], front_matter: { id: "fitbit", kind: "source", status: "draft" },
  body: text.split("---\n").at(-1) ?? text, ...more,
}); // prettier-ignore
const status = (more: Partial<KnowledgeStatus> = {}): KnowledgeStatus => ({
  available: true, repo: "in sync", name: "SripadaLab-UM/ihs-knowledge", signed_in: true,
  account: { login: "yfang", name: "Yu Fang" }, head: "abc1234", last_sync: new Date().toISOString(),
  last_error: null, ahead: 0, behind: 0, message: null, ...more,
}); // prettier-ignore
const entry = (path: string, place: string): KbEntry => ({
  path, place, title: path, summary: "", size: 10, status: null, kind: null, related: [], cohorts: [],
}); // prettier-ignore
const PAGES: Record<string, KbPage> = {
  "sources/fitbit.md": { path: "sources/fitbit.md", place: "page", head: "abc1234", text: FITBIT, front_matter: { id: "fitbit", status: "draft" }, body: "# Fitbit\n", editable: true, source_note: null },
  "generated/drift.md": {
    path: "generated/drift.md", place: "generated", head: "abc1234", text: "# Drift\n", front_matter: null, body: "# Drift\n", editable: false,
    source_note: "Generated from the database catalog (metadata only) by `datalab catalog`, and refreshed by DataLab.",
  },
}; // prettier-ignore

let client: QueryClient;
function editor(props: Partial<Parameters<typeof PageEditor>[0]> = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <PageEditor path="sources/fitbit.md" repo="SripadaLab-UM/ihs-knowledge" entries={[]} signedIn onOpen={vi.fn()} onClose={onClose} {...props} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { ...view, onClose };
}
function tab(at: string) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="knowledge/*" element={<KnowledgePage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
const source = () => screen.getByLabelText("sources/fitbit.md: its Markdown and front matter");
const type = (text: string) => fireEvent.change(source(), { target: { value: text } });

beforeEach(() => {
  chat.props = null;
  sessionStorage.clear();
  localStorage.clear();
  window.matchMedia = vi.fn(() => ({ matches: true })) as never;
  vi.mocked(knowledgeApi.status).mockReset().mockResolvedValue(status());
  vi.mocked(knowledgeApi.pages).mockReset().mockResolvedValue({
    head: "abc1234",
    pages: [entry("sources/fitbit.md", "page"), entry("generated/drift.md", "generated")],
  });
  vi.mocked(knowledgeApi.page).mockReset().mockImplementation(async (path) => PAGES[path]);
  vi.mocked(knowledgeApi.history).mockReset().mockResolvedValue([]);
  vi.mocked(knowledgeApi.edits).mockReset().mockResolvedValue([]);
  vi.mocked(knowledgeApi.startEdit).mockReset().mockResolvedValue(edit());
  vi.mocked(knowledgeApi.getEdit).mockReset().mockResolvedValue(edit());
  vi.mocked(knowledgeApi.checkEdit).mockReset().mockImplementation(async (_id, text) => checked(text));
  vi.mocked(knowledgeApi.keepEdit).mockReset().mockImplementation(async (_id, text) => edit({ text, text_sha256: "sha-new", updated_at: "t2" }));
  vi.mocked(knowledgeApi.shareEdit).mockReset().mockResolvedValue(edit({ status: "saved", commit: "c0ffee1234567" }));
  vi.mocked(knowledgeApi.discardEdit).mockReset().mockResolvedValue(edit({ status: "discarded" }));
  vi.mocked(knowledgeApi.reapplyEdit).mockReset();
});

// The page's actions ------------------------------------------------------------

it("offers Edit page and Edit with agent on an editable page", async () => {
  tab("/knowledge/sources/fitbit.md");
  expect(await screen.findByRole("button", { name: /Edit page/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Edit with agent/ })).toBeTruthy();
  expect(screen.queryByText("Read-only here.")).toBeNull();
});

it("explains where a generated page comes from, with nothing to edit", async () => {
  tab("/knowledge/generated/drift.md");
  expect(await screen.findByText("Read-only here.")).toBeTruthy();
  expect(screen.getByRole("note").textContent).toContain("datalab catalog");
  expect(screen.queryByRole("button", { name: /Edit page/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Edit with agent/ })).toBeNull();
});

it("Edit with agent opens the chat on the page, ticked, with a hint, and sends nothing", async () => {
  tab("/knowledge/sources/fitbit.md");
  fireEvent.click(await screen.findByRole("button", { name: /Edit with agent/ }));
  await waitFor(() => expect(chat.props?.request?.placeholder).toBe("Describe the change to sources/fitbit.md"));
  expect(chat.props?.context?.name).toBe("sources/fitbit.md");
  expect(screen.getByLabelText("Knowledge chat")).not.toHaveAttribute("hidden");
});

it("Edit page opens the editor, and says so when there's a draft already", async () => {
  vi.mocked(knowledgeApi.edits).mockResolvedValue([
    { id: "ke_1", path: "sources/fitbit.md", status: "draft", new_page: false, updated_at: "t1", from_conversation: null },
  ]);
  tab("/knowledge/sources/fitbit.md");
  expect(await screen.findByText("You have a draft of this page on this computer, not shared yet.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Continue editing/ }));
  expect(await screen.findByLabelText("sources/fitbit.md: its Markdown and front matter")).toBeTruthy();
  expect(knowledgeApi.startEdit).toHaveBeenCalledWith("sources/fitbit.md", false);
});

// The editor ----------------------------------------------------------------------

it("checks the text as it's typed, and shows what the check found in place", async () => {
  vi.mocked(knowledgeApi.checkEdit).mockImplementation(async (_id, text) =>
    checked(text, text.includes("P12345") ? { findings: [{ id: "f1", path: "sources/fitbit.md", rule: "study_id", severity: "data", message: "This looks like a participant or study ID.", line: 9 }] } : {}),
  ); // prettier-ignore
  editor();
  await screen.findByText(/The check passes/);
  type(FITBIT + "\nParticipant P12345\n");
  expect(await screen.findByText("This looks like a participant or study ID.")).toBeTruthy();
  expect(screen.getByText(/1 possible participant data/)).toBeTruthy();
  expect(knowledgeApi.checkEdit).toHaveBeenLastCalledWith("ke_1", FITBIT + "\nParticipant P12345\n");
});

it("previews the page as the viewer shows it, and reviews the changes against where it started", async () => {
  vi.mocked(knowledgeApi.checkEdit).mockImplementation(async (_id, text) =>
    checked(text, { shared: text.replace("status: reviewed", "status: reviewed\nreviewed_by: yfang"), body: "\n# Fitbit\n\nWear time: 2025.\n", notes: ["You're changing its status from draft to reviewed."] }),
  ); // prettier-ignore
  editor();
  await screen.findByLabelText("sources/fitbit.md: its Markdown and front matter");
  type(FITBIT.replace("status: draft", "status: reviewed") + "\nWear time: 2025.\n");
  fireEvent.click(await screen.findByRole("tab", { name: "Preview" }));
  const preview = await screen.findByTestId("kb-preview");
  await waitFor(() => expect(preview.textContent).toContain("Wear time: 2025."));
  expect(screen.getByRole("heading", { name: "fitbit" })).toBeTruthy(); // the front matter, as facts
  fireEvent.click(screen.getByRole("tab", { name: "Review changes" }));
  await waitFor(() => expect(screen.getByTestId("diff-modified").textContent).toContain("reviewed_by: yfang"));
  expect(screen.getByTestId("diff-original").textContent).toBe(FITBIT);
  expect(screen.getByText("You're changing its status from draft to reviewed.")).toBeTruthy();
});

it("keeps a draft on this computer, apart from sharing it to GitHub", async () => {
  editor();
  await screen.findByText(/The check passes/);
  type(FITBIT + "\nMore.\n");
  expect(screen.getByText("changes not kept yet")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Keep as a draft on this computer" }));
  expect(await screen.findByText("Saved on this computer (not shared).")).toBeTruthy();
  expect(knowledgeApi.keepEdit).toHaveBeenCalledWith("ke_1", FITBIT + "\nMore.\n", "t1");
  expect(knowledgeApi.shareEdit).not.toHaveBeenCalled();
  expect(screen.getByText("draft on this computer · not shared")).toBeTruthy();
  // Save & share: what was kept, what the check showed, then what GitHub has.
  await waitFor(() => expect(screen.getByRole("button", { name: "Save & share" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Save & share" }));
  expect(await screen.findByText(/Shared to GitHub/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "c0ffee1" }).getAttribute("href")).toBe("https://github.com/SripadaLab-UM/ihs-knowledge/commit/c0ffee1234567");
  expect(knowledgeApi.shareEdit).toHaveBeenCalledWith("ke_1", [], "sha-new", []);
});

it("holds Save & share until each possible participant-data finding is checked", async () => {
  const finding = { id: "f1", path: "sources/fitbit.md", rule: "study_id", severity: "data" as const, message: "This looks like a participant or study ID.", line: 9 };
  vi.mocked(knowledgeApi.checkEdit).mockImplementation(async (_id, text) => checked(text, { findings: [finding] }));
  editor();
  await screen.findByLabelText("sources/fitbit.md: its Markdown and front matter");
  type(FITBIT + "\nABC-1234\n");
  await screen.findByText("This looks like a participant or study ID.");
  const share = screen.getByRole("button", { name: "Save & share" });
  await waitFor(() => expect(screen.getByText(/Check each possible participant-data finding/)).toBeTruthy());
  expect(share).toBeDisabled();
  fireEvent.click(screen.getByRole("tab", { name: "Review changes" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /I've checked: this isn't participant data/ }));
  await waitFor(() => expect(share).toBeEnabled());
  fireEvent.click(share);
  await waitFor(() => expect(knowledgeApi.shareEdit).toHaveBeenCalledWith("ke_1", ["f1"], "sha-new", ["f1"]));
});

it("keeps what's typed across a reload, in this browser, until it's kept", async () => {
  const first = editor();
  await screen.findByText(/The check passes/);
  type(FITBIT + "\nNot kept yet.\n");
  await waitFor(() => expect(readUnsaved("ke_1")).toEqual({ text: FITBIT + "\nNot kept yet.\n", version: "t1" }));
  first.unmount();
  editor();
  await waitFor(() => expect(source()).toHaveValue(FITBIT + "\nNot kept yet.\n"));
  expect(screen.getByText("Restored changes you hadn't kept yet, from this browser.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Keep as a draft on this computer" }));
  await screen.findByText("Saved on this computer (not shared).");
  expect(readUnsaved("ke_1")).toBeNull();
});

it("shows the three versions when GitHub moved on, and reapplies the edit on the new one", async () => {
  const theirs = FITBIT.replace("# Fitbit", "# Fitbit trackers");
  vi.mocked(knowledgeApi.startEdit).mockResolvedValue(edit({ text: FITBIT + "\nMine.\n", upstream_changed: true, theirs, head: "def5678" }));
  vi.mocked(knowledgeApi.reapplyEdit).mockResolvedValueOnce({ edit: edit(), merged: "<<<<<<< Your edit\nMine\n=======\nTheirs\n>>>>>>> The version now on GitHub\n" });
  editor();
  expect(await screen.findByText(/Someone changed this page on GitHub since you started editing/)).toBeTruthy();
  expect(screen.getByLabelText("Your edit: sources/fitbit.md")).toHaveValue(FITBIT + "\nMine.\n");
  expect(screen.getByLabelText("The version now on GitHub: sources/fitbit.md")).toHaveValue(theirs);
  expect(screen.getByLabelText("Where you started: sources/fitbit.md")).toHaveValue(FITBIT);
  expect(screen.getByRole("button", { name: "Save & share" })).toBeDisabled(); // never over GitHub's version unseen
  // They overlap: both side by side, and the text to keep, marked.
  fireEvent.click(screen.getByRole("button", { name: "Reapply my edit on the new version" }));
  expect(await screen.findByText(/Your changes overlap with GitHub's/)).toBeTruthy();
  expect(knowledgeApi.reapplyEdit).toHaveBeenCalledWith("ke_1", "t1", undefined);
  const keep = screen.getByLabelText("The version to keep of sources/fitbit.md");
  expect(screen.getByRole("button", { name: "Use this text on the new version" })).toBeDisabled(); // still marked
  fireEvent.change(keep, { target: { value: theirs + "\nMine.\n" } });
  vi.mocked(knowledgeApi.reapplyEdit).mockResolvedValueOnce({ edit: edit({ base: "def5678", text: theirs + "\nMine.\n", before: theirs }), merged: null });
  fireEvent.click(screen.getByRole("button", { name: "Use this text on the new version" }));
  await waitFor(() => expect(knowledgeApi.reapplyEdit).toHaveBeenLastCalledWith("ke_1", "t1", theirs + "\nMine.\n"));
  await waitFor(() => expect(screen.queryByText(/Someone changed this page on GitHub/)).toBeNull());
  expect(source()).toHaveValue(theirs + "\nMine.\n");
});

it("opens both side by side without merging", async () => {
  vi.mocked(knowledgeApi.startEdit).mockResolvedValue(edit({ upstream_changed: true, theirs: "theirs\n" }));
  editor();
  fireEvent.click(await screen.findByRole("button", { name: "Open both side by side" }));
  expect(screen.getByLabelText("sources/fitbit.md: GitHub's version and yours")).toBeTruthy();
  expect(knowledgeApi.reapplyEdit).not.toHaveBeenCalled();
});

it("discards the draft only when asked twice", async () => {
  const { onClose } = editor();
  fireEvent.click(await screen.findByRole("button", { name: /Discard…/ }));
  expect(knowledgeApi.discardEdit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Discard this draft" }));
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(knowledgeApi.discardEdit).toHaveBeenCalledWith("ke_1");
});

it("says why when the page can't be edited", async () => {
  vi.mocked(knowledgeApi.startEdit).mockRejectedValue(new Error("index.md can't be edited here."));
  editor({ path: "index.md" });
  expect(await screen.findByRole("alert")).toHaveTextContent("index.md can't be edited here.");
});

it("never lets unkept changes typed over an older draft replace the newer one unasked", async () => {
  localStorage.setItem("datalab:kb:unsaved:ke_1", JSON.stringify({ text: FITBIT + "\nOld typing.\n", version: "t0" }));
  vi.mocked(knowledgeApi.startEdit).mockResolvedValue(edit({ text: FITBIT + "\nKept elsewhere.\n", updated_at: "t5" }));
  editor();
  expect(await screen.findByText(/typed over an older version of this draft/)).toBeTruthy();
  expect(source()).toHaveValue(FITBIT + "\nKept elsewhere.\n");
  fireEvent.click(screen.getByRole("button", { name: "Put back my unkept changes" }));
  expect(source()).toHaveValue(FITBIT + "\nOld typing.\n");
});
