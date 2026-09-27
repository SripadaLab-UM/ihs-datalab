import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/http";
import { knowledgeApi, type KnowledgeStatus, type ProposalDetail, type ProposalFile } from "@/api/knowledge";

import { languageOf, ProposalCard } from "./ProposalCard";
import type { KbProposalItem } from "./transcript";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: { status: vi.fn(), proposal: vi.fn(), edit: vi.fn(), accept: vi.fn(), reject: vi.fn() },
}));
// The editors are CodeMirror: stand-ins that show what they're given.
vi.mock("@/components/editor/DiffView", () => ({
  DiffView: ({ label, original, modified }: { label: string; original: string; modified: string }) => (
    <pre aria-label={label}>{`${original}\n=====\n${modified}`}</pre>
  ),
}));
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: ({ label, value, onChange }: { label: string; value: string; onChange?: (v: string) => void }) => (
    <textarea aria-label={label} value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

const status: KnowledgeStatus = {
  available: true, repo: "in sync", name: "SripadaLab-UM/ihs-knowledge", signed_in: true,
  account: { login: "yfang", name: "Yu Fang" }, head: "abc", last_sync: null, last_error: null,
  ahead: 0, behind: 0, message: null,
}; // prettier-ignore

const item = (more: Partial<KbProposalItem> = {}): KbProposalItem => ({
  kind: "kb_proposal",
  id: "kp_1",
  files: [
    { path: "qc/wear-time.md", change: "added", added: 3, removed: 0, flags: [] },
    { path: "sources/fitbit.md", change: "modified", added: 1, removed: 1, flags: ["status: reviewed → draft"] },
  ],
  refused: [{ path: "notes/scratch.md", reason: "it isn't part of the knowledge base's layout (see AGENTS.md)" }],
  check: { errors: 0, data: 0, warnings: 0 },
  truncated: false,
  status: "open",
  message: "",
  commit: null,
  ...more,
});

const file = (path: string, more: Partial<ProposalFile> = {}): ProposalFile => ({
  path, change: "modified", flags: [], before: `old ${path}`, agent: `new ${path}`, after: `new ${path}`,
  edited: false, left_out: false, diff: "", after_sha256: `sha-${path}`, conflict: false, theirs: null,
  theirs_state: null, resolved: false, ...more,
}); // prettier-ignore

function detail(more: { status?: string; files?: ProposalFile[]; findings?: ProposalDetail["findings"]; result?: ProposalDetail["proposal"]["result"]; commit?: string | null } = {}): ProposalDetail {
  return {
    proposal: {
      id: "kp_1", conversation_id: "c_1", status: (more.status ?? "open") as never, created_at: "", updated_at: "",
      turn: 1, base: "abc", files: [], refused: [], result: more.result ?? null, commit: more.commit ?? null,
      decided_by: more.commit ? "yfang" : null,
    },
    files: more.files ?? [file("qc/wear-time.md", { change: "added", before: null }), file("sources/fitbit.md", { flags: ["status: reviewed → draft"] })],
    findings: more.findings ?? [],
  }; // prettier-ignore
}

function show(proposal = item()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ProposalCard proposal={proposal} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(knowledgeApi.status).mockReset().mockResolvedValue(status);
  vi.mocked(knowledgeApi.proposal).mockReset().mockResolvedValue(detail());
  vi.mocked(knowledgeApi.edit).mockReset();
  vi.mocked(knowledgeApi.accept).mockReset();
  vi.mocked(knowledgeApi.reject).mockReset();
});

const saveButton = () => screen.findByRole("button", { name: "Save & share" });

describe("an open proposal", () => {
  it("shows the files, a diff of each, refused paths with reasons, and saves nothing by itself", async () => {
    show();
    expect(screen.getByRole("heading", { name: "Knowledge edits proposed (2 pages)" })).toBeTruthy();
    expect(screen.getByText("needs you")).toBeTruthy();
    expect(screen.getByText("notes/scratch.md")).toBeTruthy();
    expect(screen.getByText("it isn't part of the knowledge base's layout (see AGENTS.md)")).toBeTruthy();
    expect((await screen.findByLabelText("sources/fitbit.md: the changes")).textContent).toBe(
      "old sources/fitbit.md\n=====\nnew sources/fitbit.md",
    );
    expect(screen.getByLabelText("qc/wear-time.md: the changes").textContent).toBe("\n=====\nnew qc/wear-time.md");
    expect(screen.getByText(/status: reviewed → draft/)).toBeTruthy();
    expect(screen.getByText(/Passes: front matter/)).toBeTruthy();
    expect(knowledgeApi.accept).not.toHaveBeenCalled();
    expect(knowledgeApi.reject).not.toHaveBeenCalled();
    expect(knowledgeApi.edit).not.toHaveBeenCalled();
  });

  it("saves and shares on the person's click, and then shows the commit", async () => {
    vi.mocked(knowledgeApi.accept).mockResolvedValue(
      detail({ status: "saved", commit: "bc2ac1b0aa", result: { state: "saved", message: "Saved and shared with the lab.", commit: "bc2ac1b0aa", findings: [], conflicts: [], after_rebase: false } }),
    );
    show();
    fireEvent.click(await saveButton());
    // With what the person saw: each file's text (as a digest) and the check's findings.
    await waitFor(() =>
      expect(knowledgeApi.accept).toHaveBeenCalledWith(
        "kp_1", [], { "qc/wear-time.md": "sha-qc/wear-time.md", "sources/fitbit.md": "sha-sources/fitbit.md" }, [],
      ),
    ); // prettier-ignore
    expect(await screen.findByText("saved and shared")).toBeTruthy();
    const link = screen.getByRole("link", { name: /Commit bc2ac1b on GitHub/ });
    expect(link.getAttribute("href")).toBe("https://github.com/SripadaLab-UM/ihs-knowledge/commit/bc2ac1b0aa");
    expect(screen.getByText(/by @yfang/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save & share" })).toBeNull();
  });

  it("holds the save until each possible participant-data hit is confirmed, and sends which", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(
      detail({ findings: [{ id: "f1", path: "qc/wear-time.md", rule: "study_id", severity: "data", message: "This looks like a participant or study ID.", line: 17 }] }),
    );
    vi.mocked(knowledgeApi.accept).mockResolvedValue(detail({ status: "saved", commit: "bc2ac1b" }));
    show();
    const save = await saveButton();
    expect(save).toHaveProperty("disabled", true);
    expect(screen.getByText(/Check the possible participant data first/)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /qc\/wear-time.md:17.*isn't participant data/ }));
    expect(save).toHaveProperty("disabled", false);
    fireEvent.click(save);
    await waitFor(() => expect(knowledgeApi.accept).toHaveBeenCalledWith("kp_1", ["f1"], expect.anything(), ["f1"]));
  });

  it("can't be saved with an error, or by someone signed out", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(
      detail({ findings: [{ id: "e1", path: "sources/fitbit.md", rule: "front_matter", severity: "error", message: "Its front matter isn't valid YAML (line 5).", line: 5 }] }),
    );
    const view = show();
    expect(await saveButton()).toHaveProperty("disabled", true);
    expect(screen.getByText(/Fix the error the check found first/)).toBeTruthy();
    expect(screen.getByText(/front matter isn't valid YAML/)).toBeTruthy();
    view.unmount();

    vi.mocked(knowledgeApi.status).mockResolvedValue({ ...status, signed_in: false, repo: "signed out" });
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(detail());
    show();
    await screen.findByText(/Sign in to GitHub \(in Settings\)/);
    expect(await saveButton()).toHaveProperty("disabled", true);
    expect(screen.getByRole("link", { name: "Open Settings to sign in" }).getAttribute("href")).toBe("/settings");
  });

  it("lets the person edit a file's text, leave a file out, or discard it all", async () => {
    vi.mocked(knowledgeApi.edit).mockResolvedValue(detail({ files: [file("qc/wear-time.md", { edited: true, after: "mine" }), file("sources/fitbit.md")] }));
    vi.mocked(knowledgeApi.reject).mockResolvedValue(detail({ status: "rejected" }));
    show();
    await screen.findByLabelText("sources/fitbit.md: the changes");
    fireEvent.click(inFile("qc/wear-time.md").getByRole("button", { name: /Edit/ }));
    const editor = screen.getByLabelText("Your version of qc/wear-time.md");
    expect((editor as HTMLTextAreaElement).value).toBe("new qc/wear-time.md");
    fireEvent.change(editor, { target: { value: "mine" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep my edit" }));
    await waitFor(() => expect(knowledgeApi.edit).toHaveBeenCalledWith("kp_1", { "qc/wear-time.md": "mine" }));
    expect(await screen.findByText("your edit")).toBeTruthy();

    fireEvent.click(inFile("sources/fitbit.md").getByRole("button", { name: "Leave out" }));
    await waitFor(() => expect(knowledgeApi.edit).toHaveBeenCalledWith("kp_1", { "sources/fitbit.md": null }));

    fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
    await waitFor(() => expect(knowledgeApi.reject).toHaveBeenCalledWith("kp_1"));
    expect(await screen.findByText("Discarded. Nothing was shared.")).toBeTruthy();
    expect(knowledgeApi.accept).not.toHaveBeenCalled();
  });
});

describe("Save & share's other end states", () => {
  it("conflict: shows GitHub's version against the person's, and saves only once it's resolved", async () => {
    // Edited before it conflicted: that's not a resolution.
    const conflicted = file("qc/midnight-sleep.md", { conflict: true, theirs: "theirs text", theirs_state: "text", after: "ours text", edited: true });
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(
      detail({ status: "conflict", files: [conflicted], result: { state: "conflict", message: "Someone else changed it.", commit: null, findings: [], conflicts: ["qc/midnight-sleep.md"], after_rebase: false } }),
    );
    vi.mocked(knowledgeApi.edit).mockResolvedValue(
      detail({ status: "conflict", files: [{ ...conflicted, resolved: true, after: "theirs text" }], result: { state: "conflict", message: "", commit: null, findings: [], conflicts: ["qc/midnight-sleep.md"], after_rebase: false } }),
    );
    show(item({ status: "conflict" }));
    expect(screen.getByText("someone else changed it")).toBeTruthy();
    expect((await screen.findByLabelText("qc/midnight-sleep.md: GitHub's version now, and yours")).textContent).toBe(
      "theirs text\n=====\nours text",
    );
    expect(await saveButton()).toHaveProperty("disabled", true);
    expect(screen.getByText(/Resolve the file someone else changed on GitHub first/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Resolve/ }));
    fireEvent.click(screen.getByRole("button", { name: "Start from GitHub's version" }));
    expect((screen.getByLabelText("Your version of qc/midnight-sleep.md") as HTMLTextAreaElement).value).toBe("theirs text");
    fireEvent.click(screen.getByRole("button", { name: "Keep this version" }));
    await waitFor(() => expect(knowledgeApi.edit).toHaveBeenCalledWith("kp_1", { "qc/midnight-sleep.md": "theirs text" }));
    await waitFor(async () => expect(await saveButton()).toHaveProperty("disabled", false));
  });

  it("check_failed after a rebase: shows what someone else's change broke", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(
      detail({
        status: "check_failed",
        result: {
          state: "check_failed", message: "The check failed.", commit: null, conflicts: [], after_rebase: true,
          findings: [{ id: "x", path: "qc/wear-time.md", rule: "related", severity: "error", message: "related: qc/midnight-sleep doesn't exist.", line: null }],
        },
      }),
    ); // prettier-ignore
    show(item({ status: "check_failed" }));
    expect(screen.getByText("the check stopped it")).toBeTruthy();
    expect(await screen.findByText("After bringing in others' changes")).toBeTruthy();
    expect(screen.getByText(/qc\/midnight-sleep doesn't exist/)).toBeTruthy();
    expect(await saveButton()).toHaveProperty("disabled", false); // it can be tried again after a fix
  });

  it("failed: says nothing was shared, and can be tried again", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(
      detail({ status: "failed", result: { state: "failed", message: "Saving failed (GitError).", commit: null, findings: [], conflicts: [], after_rebase: false } }),
    );
    show(item({ status: "failed", message: "Saving failed (GitError)." }));
    expect(await screen.findByText("Saving failed (GitError). Nothing was shared; you can try again.")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Try again" })).toHaveProperty("disabled", false);
  });

  it("a decided proposal is folded, with its changes a click away", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(detail({ status: "superseded" }));
    show(item({ status: "superseded" }));
    expect(screen.getByText("Replaced by a newer proposal, further down.")).toBeTruthy();
    expect(knowledgeApi.proposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Show the changes" }));
    expect(await screen.findByLabelText("sources/fitbit.md: the changes")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save & share" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
  });
});

describe("what's saved is what's on screen", () => {
  it("won't save while an edit isn't kept, and saves the kept text", async () => {
    vi.mocked(knowledgeApi.edit).mockResolvedValue(
      detail({ files: [file("qc/wear-time.md", { edited: true, after: "mine", after_sha256: "sha-mine" }), file("sources/fitbit.md")] }),
    );
    vi.mocked(knowledgeApi.accept).mockResolvedValue(detail({ status: "saved", commit: "bc2ac1b" }));
    show();
    const save = await saveButton();
    fireEvent.click(inFile("qc/wear-time.md").getByRole("button", { name: /Edit/ }));
    // The editor open but untouched doesn't block.
    expect(save).toHaveProperty("disabled", false);
    fireEvent.change(screen.getByLabelText("Your version of qc/wear-time.md"), { target: { value: "mine" } });
    expect(save).toHaveProperty("disabled", true);
    expect(screen.getByText(/Keep or cancel your edit of qc\/wear-time.md first/)).toBeTruthy();
    fireEvent.click(save);
    expect(knowledgeApi.accept).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Keep my edit" }));
    await waitFor(() => expect(save).toHaveProperty("disabled", false));
    fireEvent.click(save);
    await waitFor(() =>
      expect(knowledgeApi.accept).toHaveBeenCalledWith(
        "kp_1", [], { "qc/wear-time.md": "sha-mine", "sources/fitbit.md": "sha-sources/fitbit.md" }, [],
      ),
    ); // prettier-ignore
  });

  it("cancelling an edit drops it, and saving is open again", async () => {
    show();
    const save = await saveButton();
    fireEvent.click(inFile("qc/wear-time.md").getByRole("button", { name: /Edit/ }));
    fireEvent.change(screen.getByLabelText("Your version of qc/wear-time.md"), { target: { value: "mine" } });
    expect(save).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(save).toHaveProperty("disabled", false);
    expect(knowledgeApi.edit).not.toHaveBeenCalled();
  });

  it("when it changed in another window, says so and shows it as it is now", async () => {
    vi.mocked(knowledgeApi.accept).mockRejectedValue(
      new ApiError(409, "This proposal changed since you looked at it (in another window?). Nothing was saved: check it again, then save."),
    );
    show();
    fireEvent.click(await saveButton());
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringMatching(/changed since you looked/));
    await waitFor(() => expect(knowledgeApi.proposal).toHaveBeenCalledTimes(2));
  });

  it("keeps focus on the card when it folds after saving", async () => {
    vi.mocked(knowledgeApi.accept).mockResolvedValue(detail({ status: "saved", commit: "bc2ac1b" }));
    show();
    const save = await saveButton();
    save.focus();
    fireEvent.click(save);
    await screen.findByText("saved and shared");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("region", { name: "Knowledge edits proposed (2 pages)" })));
  });
});

describe("conflicts GitHub's side of which is gone or isn't text", () => {
  const conflict = (f: ProposalFile) =>
    detail({ status: "conflict", files: [f], result: { state: "conflict", message: "", commit: null, findings: [], conflicts: [f.path], after_rebase: false } });

  it("a file GitHub deleted can be left out, or resolved with text", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(conflict(file("qc/midnight-sleep.md", { conflict: true, theirs_state: "deleted", after: "ours" })));
    vi.mocked(knowledgeApi.edit).mockResolvedValue(conflict(file("qc/midnight-sleep.md", { conflict: true, theirs_state: "deleted", after: "ours", resolved: true })));
    show(item({ status: "conflict" }));
    expect(await screen.findByText("deleted on GitHub since")).toBeTruthy();
    expect(screen.getByText(/Leave it out to keep it deleted/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start from GitHub's version" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Resolve/ }));
    expect(screen.queryByRole("button", { name: "Start from GitHub's version" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Keep this version" }));
    await waitFor(() => expect(knowledgeApi.edit).toHaveBeenCalledWith("kp_1", { "qc/midnight-sleep.md": "ours" }));
    expect(await screen.findByText("resolved")).toBeTruthy();
  });

  it("a file the agent deleted that GitHub changed can be resolved", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(
      conflict(file("qc/midnight-sleep.md", { change: "deleted", after: null, after_sha256: null, conflict: true, theirs: "theirs", theirs_state: "text" })),
    );
    show(item({ status: "conflict" }));
    fireEvent.click(await screen.findByRole("button", { name: /Resolve/ }));
    expect((screen.getByLabelText("Your version of qc/midnight-sleep.md") as HTMLTextAreaElement).value).toBe("theirs");
  });

  it("GitHub's version that isn't text can't be resolved here", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(conflict(file("qc/midnight-sleep.md", { conflict: true, theirs_state: "not text" })));
    show(item({ status: "conflict" }));
    expect(await screen.findByText(/GitHub's version isn't text/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Resolve/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Leave out" })).toBeTruthy();
    expect(screen.queryByLabelText(/GitHub's version now, and yours/)).toBeNull();
  });

  it("asks before GitHub's version replaces what the person wrote", async () => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(conflict(file("qc/midnight-sleep.md", { conflict: true, theirs: "theirs", theirs_state: "text", after: "ours" })));
    show(item({ status: "conflict" }));
    fireEvent.click(await screen.findByRole("button", { name: /Resolve/ }));
    const editor = screen.getByLabelText("Your version of qc/midnight-sleep.md") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "both, merged by hand" } });
    fireEvent.click(screen.getByRole("button", { name: "Start from GitHub's version" }));
    expect(editor.value).toBe("both, merged by hand");
    expect(screen.getByText(/That replaces what you've written here/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Replace your text with GitHub's version" }));
    expect(editor.value).toBe("theirs");
  });
});

describe("cards that can't be acted on", () => {
  it.each([
    ["withdrawn", "The agent undid these edits."],
    ["superseded", "Replaced by a newer proposal, further down."],
    ["rejected", "Discarded. Nothing was shared."],
  ])("%s", async (status, text) => {
    vi.mocked(knowledgeApi.proposal).mockResolvedValue(detail({ status }));
    show(item({ status }));
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save & share" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
    expect(knowledgeApi.proposal).not.toHaveBeenCalled();
  });

  it("saving: says so, offers nothing to press, and follows it until it ends", async () => {
    vi.mocked(knowledgeApi.proposal)
      .mockResolvedValueOnce(detail({ status: "saving" }))
      .mockResolvedValue(detail({ status: "failed", result: { state: "failed", message: "DataLab stopped while saving. Try again.", commit: null, findings: [], conflicts: [], after_rebase: false } }));
    show(item({ status: "saving" }));
    expect(screen.getByText("saving…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save & share" })).toBeNull();
    // A restart ended it: it can be tried again.
    expect(await screen.findByText("DataLab stopped while saving. Try again. Nothing was shared; you can try again.", {}, { timeout: 4000 })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Review the changes" }));
    expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

it("edits each file in its own language", () => {
  expect(["a.md", "s.yml", "x.R", "q.sql", "notes.txt"].map(languageOf)).toEqual(["markdown", "yaml", "r", "sql", "text"]);
});

const inFile = (path: string) => within(screen.getByRole("article", { name: path }));
