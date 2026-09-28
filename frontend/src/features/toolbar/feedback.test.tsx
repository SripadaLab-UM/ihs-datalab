import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { exportsApi } from "@/api/exports";
import { type SupportReport, type SupportReportDetail, supportApi } from "@/api/support";
import { clearTrail, recordFailedRequest } from "@/lib/supportTrail";

import { EMPTY_DRAFT, FeedbackDialog, type FeedbackDraft, filesProblem, MAX_FILE_BYTES, STATE_WORDS } from "./FeedbackDialog";

vi.mock("@/api/support", () => ({
  supportApi: {
    status: vi.fn(),
    github: vi.fn(),
    preview: vi.fn(),
    save: vi.fn(),
    reports: vi.fn(),
    report: vi.fn(),
    bundleUrl: (id: string) => `/api/support/reports/${id}/bundle`,
    remove: vi.fn(),
    saveToFolder: vi.fn(),
    send: vi.fn(),
  },
}));
vi.mock("@/api/exports", () => ({ exportsApi: { destinations: vi.fn() } }));

const ID = "DL-20260928-7F3K";
const CONTENTS = {
  files: [
    { path: "summary.md", bytes: 300, sha256: "a".repeat(64) },
    { path: "diagnostics.json", bytes: 900, sha256: "b".repeat(64) },
    { path: "attachments/attachment-1.png", bytes: 68, sha256: "c".repeat(64) },
    { path: "manifest.json", bytes: 400, sha256: "d".repeat(64) },
  ],
  summary: `# DataLab report ${ID}\n\n## What happened\n\nIt broke.`,
  diagnostics: '{\n  "report_id": "DL-20260928-7F3K",\n  "trail": {}\n}\n',
  manifest: '{\n  "files": []\n}\n',
};
const EMAIL = {
  contact: "Ali <ali@umich.edu>",
  to: "ali@umich.edu",
  subject: `DataLab report ${ID}`,
  body: `Please find DataLab report ${ID} attached.\n\nAttach the file: ${ID}.zip`,
  mailto: `mailto:ali@umich.edu?subject=DataLab%20report%20${ID}&body=x`,
};

function report(change: Partial<SupportReportDetail> = {}): SupportReportDetail {
  return {
    report_id: ID,
    kind: "bug",
    created_at: "2026-09-28T14:00:00Z",
    headline: "It broke.",
    saved_at: "2026-09-28T14:01:00Z",
    zip_name: `${ID}.zip`,
    zip_bytes: 2048,
    zip_sha256: "e".repeat(64),
    attachments: [],
    states: ["saved_locally"],
    folders: [],
    github: null,
    email: EMAIL,
    contents: CONTENTS,
    ...change,
  };
}

const DROPBOX_FOLDER = {
  name: "Lab Dropbox",
  file: `/Users/me/Library/CloudStorage/Dropbox/reports/${ID}.zip`,
  where: `~/Library/CloudStorage/Dropbox/reports/${ID}.zip`,
  saved_at: "2026-09-28T14:02:00Z",
  saved_to: "Saved to Lab Dropbox (on this computer)",
  sync_provider: "dropbox",
  sync_note:
    "Dropbox will upload it when its app is running and signed in. DataLab can't confirm the upload, and it isn't sent to anyone until you email it.",
  practice: false,
};

const REPO_OK = {
  configured: true,
  repo: "SripadaLab-UM/ihs-support",
  available: true,
  signed_in: true,
  message: null,
  private: true,
  can_write: true,
};

function Harness({ initial = EMPTY_DRAFT }: { initial?: FeedbackDraft }) {
  const [draft, setDraft] = useState<FeedbackDraft>(initial);
  const [open, setOpen] = useState(true);
  return (
    <>
      {open ? <FeedbackDialog draft={draft} onDraft={setDraft} onClose={() => setOpen(false)} /> : null}
      <button onClick={() => setOpen(true)}>Open again</button>
    </>
  );
}

function show(path = "/workspace/c_0123456789abcdef?q=secret-query#frag") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const png = () => new File([new Uint8Array([137, 80, 78, 71])], "shot.png", { type: "image/png" });

beforeEach(() => {
  clearTrail();
  vi.mocked(supportApi.status)
    .mockReset()
    .mockResolvedValue({
      profile: "real",
      practice: false,
      contact: { contact: EMAIL.contact, email: EMAIL.to },
      github: REPO_OK,
    });
  vi.mocked(supportApi.github).mockReset().mockResolvedValue(REPO_OK);
  vi.mocked(supportApi.reports).mockReset().mockResolvedValue([]);
  vi.mocked(supportApi.report).mockReset().mockResolvedValue(report());
  vi.mocked(supportApi.preview).mockReset().mockResolvedValue({
    draft_id: "draft-1",
    report_id: ID,
    created_at: "2026-09-28T14:00:00Z",
    zip_name: `${ID}.zip`,
    zip_bytes: 2048,
    zip_sha256: "e".repeat(64),
    contents: CONTENTS,
    warnings: ["You chose a file: DataLab doesn't look inside it."],
  });
  vi.mocked(supportApi.save).mockReset().mockResolvedValue(report());
  vi.mocked(supportApi.remove).mockReset().mockResolvedValue(undefined);
  vi.mocked(supportApi.saveToFolder)
    .mockReset()
    .mockResolvedValue(report({ states: ["saved_locally", "saved_to_folder"], folders: [DROPBOX_FOLDER] }));
  vi.mocked(supportApi.send).mockReset();
  vi.mocked(exportsApi.destinations)
    .mockReset()
    .mockResolvedValue([
      {
        id: "dest_1",
        name: "Lab Dropbox",
        path: "/Users/me/Library/CloudStorage/Dropbox/reports",
        where: "~/Library/CloudStorage/Dropbox/reports",
        available: true,
        status: "ready",
        status_message: null,
        warning: null,
        location: "sync_folder",
        sync_provider: "dropbox",
        sync_provider_name: "Dropbox",
        location_note: "Inside your Dropbox folder: Dropbox will upload it when its app is running and signed in.",
        offered: true,
        workflow_keys: [],
        practice: false,
      },
    ]);
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:preview"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});
afterEach(() => vi.restoreAllMocks());

async function fillBug(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByRole("textbox", { name: "What happened" }), { target: { value: "It broke." } });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "What did you expect" }), {
    target: { value: "The rows." },
  });
}

// ------------------------------------------------------------------ the form

it("the form: a bug asks what happened and what was expected; a suggestion only what would help", async () => {
  show();
  const dialog = await screen.findByRole("dialog", { name: "Send feedback" });
  expect(within(dialog).getByRole("note")).toHaveTextContent(
    "Don't include participant data: no names, IDs, dates or values from the study database.",
  );
  expect(within(dialog).getByRole("textbox", { name: "What did you expect" })).toBeInTheDocument();
  expect(within(dialog).getByRole("textbox", { name: "Steps to get there (optional)" })).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Preview report" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Say what happened.");
  expect(supportApi.preview).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("radio", { name: "Suggestion" }));
  expect(within(dialog).queryByRole("textbox", { name: "What did you expect" })).not.toBeInTheDocument();
  expect(within(dialog).getByRole("textbox", { name: "What would help" })).toBeInTheDocument();
  // No old mailto-with-diagnostics: nothing to include by ticking a box.
  expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("link", { name: /Email/ })).not.toBeInTheDocument();
});

it("the draft is kept when the dialog is closed and opened again", async () => {
  show();
  const dialog = await screen.findByRole("dialog", { name: "Send feedback" });
  fireEvent.click(within(dialog).getByRole("radio", { name: "Suggestion" }));
  fireEvent.change(within(dialog).getByRole("textbox", { name: "What would help" }), {
    target: { value: "Half-written idea" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open again" }));
  const again = await screen.findByRole("dialog", { name: "Send feedback" });
  expect(within(again).getByRole("textbox", { name: "What would help" })).toHaveValue("Half-written idea");
});

it("files: listed with name and size, images previewed, removable, with a warning; never captured", async () => {
  show();
  const dialog = await screen.findByRole("dialog", { name: "Send feedback" });
  expect(dialog).toHaveTextContent("You're responsible for what's in them");
  expect(dialog).toHaveTextContent("DataLab never takes a screenshot itself.");
  const input = within(dialog).getByLabelText("Choose files to include");
  const notes = new File(["some notes"], "notes.txt", { type: "text/plain" });
  fireEvent.change(input, { target: { files: [png(), notes] } });
  const list = within(dialog).getByRole("list", { name: "Files to include" });
  expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["shot.png4 B", "notes.txt10 B"]);
  expect(within(list).getByRole("img", { name: "Preview of shot.png" })).toHaveAttribute("src", "blob:preview");
  fireEvent.click(within(list).getByRole("button", { name: "Remove notes.txt" }));
  expect(within(list).getAllByRole("listitem")).toHaveLength(1);
});

it("files: too large is said before anything is sent", () => {
  const big = new File(["x"], "huge.mov");
  Object.defineProperty(big, "size", { value: MAX_FILE_BYTES + 1 });
  expect(filesProblem([big])).toBe("huge.mov is larger than 5.0 MB.");
  expect(filesProblem([png(), png(), png(), png(), png(), png()])).toBe("Attach at most 5 files.");
  expect(filesProblem([png()])).toBeNull();
});

// ------------------------------------------------------------------ preview and save

it("preview: sends the path without its query, the browser, the trail and the files; shows every file", async () => {
  recordFailedRequest("GET", "/api/conversations/c_1/files/outputs/x.csv?token=abc", 404);
  show();
  const dialog = await screen.findByRole("dialog", { name: "Send feedback" });
  await fillBug(dialog);
  fireEvent.change(within(dialog).getByLabelText("Choose files to include"), { target: { files: [png()] } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Preview report" }));
  const preview = await screen.findByRole("dialog", { name: "Check the report" });
  const sent = vi.mocked(supportApi.preview).mock.calls[0][0];
  expect(sent.route).toBe("/workspace/c_0123456789abcdef");
  expect(JSON.stringify(sent)).not.toContain("secret-query");
  expect(JSON.stringify(sent)).not.toContain("token=abc");
  expect(sent.kind).toBe("bug");
  expect(sent.happened).toBe("It broke.");
  expect(sent.expected).toBe("The rows.");
  expect(sent.user_agent).toBe(navigator.userAgent);
  expect(sent.client_trail).toEqual([
    expect.objectContaining({ kind: "request", method: "GET", path: "/api/conversations/c_1/files/outputs/x.csv", status: 404 }),
  ]);
  expect(sent.attachments).toEqual([{ name: "shot.png", data_base64: btoa(String.fromCharCode(137, 80, 78, 71)) }]);

  expect(preview).toHaveTextContent(`This is everything in report ${ID}`);
  expect(preview).toHaveTextContent("Nothing has been saved or sent yet.");
  const files = within(preview).getByRole("region", { name: "Files in the report" });
  expect(files).toHaveTextContent("attachments/attachment-1.png");
  expect(files).not.toHaveTextContent("shot.png");
  expect(within(preview).getByRole("list", { name: "Your files in the report" })).toHaveTextContent(
    "attachments/attachment-1.png is your shot.png: its own name isn't included.",
  );
  expect(within(preview).getAllByRole("note")[0]).toHaveTextContent("Don't include participant data");
  expect(files).toHaveTextContent("manifest.json");
  expect(within(preview).getByRole("region", { name: "summary.md" })).toHaveTextContent("## What happened");
  expect(within(preview).getByRole("region", { name: "diagnostics.json" })).toHaveTextContent('"report_id"');
  expect(within(preview).getByRole("region", { name: "manifest.json" })).toHaveTextContent('"files"');
  expect(within(preview).getByRole("list", { name: "Check before saving" })).toHaveTextContent("doesn't look inside");
  expect(within(preview).getByRole("img", { name: "Preview of shot.png" })).toBeInTheDocument();

  fireEvent.click(within(preview).getByRole("button", { name: "Save report" }));
  await waitFor(() => expect(supportApi.save).toHaveBeenCalledWith("draft-1"));
  const saved = await screen.findByRole("dialog", { name: `Report ${ID}` });
  expect(within(saved).getByRole("list", { name: "Where this report stands" })).toHaveTextContent(
    "Saved on this computer, in DataLab's data folder.",
  );
  expect(within(saved).getByRole("link", { name: "Download ZIP" })).toHaveAttribute(
    "href",
    `/api/support/reports/${ID}/bundle`,
  );
});

it("preview: Back to edit keeps what was written", async () => {
  show();
  const dialog = await screen.findByRole("dialog", { name: "Send feedback" });
  await fillBug(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Preview report" }));
  const preview = await screen.findByRole("dialog", { name: "Check the report" });
  fireEvent.click(within(preview).getByRole("button", { name: "Back to edit" }));
  const back = await screen.findByRole("dialog", { name: "Send feedback" });
  expect(within(back).getByRole("textbox", { name: "What happened" })).toHaveValue("It broke.");
  expect(supportApi.save).not.toHaveBeenCalled();
});

// ------------------------------------------------------------------ states and sending

async function openSaved(detail: SupportReportDetail) {
  vi.mocked(supportApi.reports).mockResolvedValue([detail as SupportReport]);
  vi.mocked(supportApi.report).mockResolvedValue(detail);
  show();
  const form = await screen.findByRole("dialog", { name: "Send feedback" });
  fireEvent.click(await within(form).findByRole("button", { name: "Saved reports (1)" }));
  const list = await screen.findByRole("list", { name: "Saved reports" });
  fireEvent.click(within(list).getByRole("button", { name: new RegExp(ID) }));
  const dialog = await screen.findByRole("dialog", { name: `Report ${ID}` });
  await within(dialog).findByRole("list", { name: "Where this report stands" });
  return dialog;
}

it("a folder copy says saved to the folder, and who uploads it: never delivered", async () => {
  const dialog = await openSaved(report());
  const folder = within(dialog).getByRole("region", { name: "Save to a folder, then email it" });
  expect(await within(folder).findByText(/Nobody gets it until you email it\./)).toBeInTheDocument();
  fireEvent.click(within(folder).getByRole("button", { name: "Save to Lab Dropbox" }));
  await waitFor(() => expect(supportApi.saveToFolder).toHaveBeenCalledWith(ID, "dest_1"));
  const states = within(dialog).getByRole("list", { name: "Where this report stands" });
  await waitFor(() => expect(states).toHaveTextContent("Saved to Lab Dropbox (on this computer)."));
  expect(states).toHaveTextContent("Dropbox will upload it when its app is running and signed in.");
  expect(states.textContent).not.toMatch(/deliver/i);
  expect(folder.textContent).not.toMatch(/deliver/i);
  // Next: email it, with the file's full path to find it.
  expect(folder).toHaveTextContent(DROPBOX_FOLDER.file);
  expect(folder).toHaveTextContent("Next: email this file to Ali <ali@umich.edu>.");
  expect(within(folder).getByRole("link", { name: "Write the email" })).toHaveAttribute("href", EMAIL.mailto);
  expect(folder).toHaveTextContent("Attach the file yourself: an email link can't attach it.");
  expect(STATE_WORDS.saved_to_folder).not.toMatch(/deliver/i);
});

it("a send that can't happen now is pending, with the reason and Retry", async () => {
  const pending = report({
    states: ["saved_locally", "pending_retry"],
    github: {
      state: "pending",
      repo: REPO_OK.repo,
      reason: "GitHub couldn't be reached: this computer may be offline.",
      attempts: 1,
    },
  });
  vi.mocked(supportApi.send).mockResolvedValue(
    report({
      states: ["saved_locally", "confirmed_delivery"],
      github: {
        state: "confirmed",
        repo: REPO_OK.repo,
        attempts: 2,
        commit_sha: "0123456789abcdef",
        html_url: `https://github.com/${REPO_OK.repo}/blob/main/reports/2026/09/${ID}.zip`,
      },
    }),
  );
  const dialog = await openSaved(pending);
  const states = within(dialog).getByRole("list", { name: "Where this report stands" });
  expect(states).toHaveTextContent(`Pending retry: not sent to ${REPO_OK.repo} yet. GitHub couldn't be reached`);
  const lab = within(dialog).getByRole("region", { name: "Send to the lab" });
  expect(await within(lab).findByText(/\(private\)/)).toBeInTheDocument();
  fireEvent.click(within(lab).getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(supportApi.send).toHaveBeenCalledWith(ID));
  await waitFor(() => expect(states).toHaveTextContent(`Delivered to ${REPO_OK.repo}: GitHub confirmed commit 0123456789.`));
  expect(within(states).getByRole("link", { name: "View it on GitHub" })).toHaveAttribute(
    "href",
    `https://github.com/${REPO_OK.repo}/blob/main/reports/2026/09/${ID}.zip`,
  );
  expect(within(lab).getByText("GitHub has it: nothing more to do.")).toBeInTheDocument();
});

it("a link GitHub gave is shown only if it's on github.com", async () => {
  const dialog = await openSaved(
    report({
      states: ["saved_locally", "confirmed_delivery"],
      github: { state: "confirmed", repo: REPO_OK.repo, attempts: 1, commit_sha: "abc", html_url: "javascript:alert(1)" },
    }),
  );
  expect(within(dialog).getByRole("list", { name: "Where this report stands" })).toHaveTextContent("Delivered to");
  expect(within(dialog).queryByRole("link", { name: "View it on GitHub" })).not.toBeInTheDocument();
});

it("the destination is shown before sending, and a public repository can't be sent to", async () => {
  vi.mocked(supportApi.github).mockResolvedValue({
    ...REPO_OK,
    private: false,
    available: false,
    message: "SripadaLab-UM/ihs-support is a public repository, so DataLab won't send reports there.",
  });
  const dialog = await openSaved(report());
  const lab = within(dialog).getByRole("region", { name: "Send to the lab" });
  expect(lab).toHaveTextContent("Goes to SripadaLab-UM/ihs-support");
  expect(await within(lab).findByText(/is a public repository/)).toBeInTheDocument();
  expect(within(lab).getByRole("button", { name: "Send to the lab" })).toBeDisabled();
});

it("without the support repo, only the folder: the maintainer is asked to set it up", async () => {
  vi.mocked(supportApi.status).mockResolvedValue({
    profile: "practice",
    practice: true,
    contact: { contact: null, email: null },
    github: {
      configured: false,
      repo: null,
      available: false,
      signed_in: false,
      message: "Practice DataLab saves reports to its own folder only.",
    },
  });
  const dialog = await openSaved(report());
  const lab = within(dialog).getByRole("region", { name: "Send to the lab" });
  expect(await within(lab).findByText("Practice DataLab saves reports to its own folder only.")).toBeInTheDocument();
  expect(within(lab).queryByRole("button")).not.toBeInTheDocument();
  expect(supportApi.github).not.toHaveBeenCalled();
});

it("saved reports reopen, show their contents, and can be deleted", async () => {
  const dialog = await openSaved(report());
  fireEvent.click(within(dialog).getByRole("button", { name: "Show contents" }));
  expect(within(dialog).getByRole("region", { name: "summary.md" })).toHaveTextContent(`# DataLab report ${ID}`);
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete…" }));
  vi.mocked(supportApi.reports).mockResolvedValue([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(supportApi.remove).toHaveBeenCalledWith(ID));
  expect(await screen.findByText("No saved reports.")).toBeInTheDocument();
});
