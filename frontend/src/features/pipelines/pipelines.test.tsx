import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import {
  type PipelineProposal,
  type PipelineProposalDetail,
  pipelinesApi,
  type PipelinesStatus,
  type PipelineTest,
} from "@/api/pipelines";

import { folderTree, languageOf, proposalChip, repoLine, testLine } from "./pipelines";
import { PipelinesPage } from "./PipelinesPage";
import { ProposalView } from "./ProposalView";

vi.mock("@/api/pipelines", () => ({
  pipelinesApi: {
    status: vi.fn(),
    sync: vi.fn(),
    files: vi.fn(),
    file: vi.fn(),
    proposals: vi.fn(),
    proposal: vi.fn(),
    test: vi.fn(),
    accept: vi.fn(),
    reject: vi.fn(),
    testLog: vi.fn(),
  },
}));
vi.mock("@/api/client", () => ({ api: { conversations: vi.fn(async () => []) } }));
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: ({ mode }: { mode: string }) => <p>The {mode} chat</p>,
}));

const status: PipelinesStatus = {
  available: true,
  repo: "in sync",
  name: "SripadaLab-UM/ihs-pipelines",
  signed_in: true,
  account: { login: "yfang", name: "Yu Fang" },
  head: "abc1234def",
  last_sync: "2026-09-27T10:00:00+00:00",
  last_error: null,
  ahead: 0,
  behind: 0,
  message: null,
};

const passed: PipelineTest = {
  id: "pt_1",
  status: "passed",
  started_at: "2026-09-27T10:00:00+00:00",
  finished_at: "2026-09-27T10:01:00+00:00",
  commit: "c0ffee",
  message: "All 3 tests passed.",
  tests: 3,
  passed: 3,
  failed: 0,
  skipped: 0,
  errors: 0,
  warnings: 0,
  files: [],
  more_files: 0,
  failures: [],
};

const proposal: PipelineProposal = {
  id: "pp_1",
  conversation_id: "c1",
  conversation_title: "Weekly steps",
  status: "open",
  created_at: "2026-09-27T10:00:00+00:00",
  updated_at: "2026-09-27T10:00:00+00:00",
  turn: 2,
  base: "abc1234def",
  files: [{ path: "ihsDataR/R/steps.R", change: "modified" }],
  refused: [{ path: "AGENTS.md", reason: "only changes to ihsDataR/ and workflows/ are proposed" }],
  result: null,
  commit: null,
  decided_by: null,
  test: null,
};

const detail = (over: Partial<PipelineProposal> = {}, findings: PipelineProposalDetail["findings"] = []): PipelineProposalDetail => ({
  proposal: { ...proposal, ...over },
  files: [
    {
      path: "ihsDataR/R/steps.R",
      change: "modified",
      before: "x <- 1\n",
      after: "x <- 7\n",
      binary: false,
      diff: "",
    },
  ],
  findings,
});

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(pipelinesApi.status).mockResolvedValue(status);
  vi.mocked(pipelinesApi.files).mockResolvedValue({
    head: "abc1234def",
    files: [
      { path: "ihsDataR/R/steps.R", size: 7 },
      { path: "ihsDataR/DESCRIPTION", size: 30 },
      { path: "workflows/weekly.yaml", size: 20 },
      { path: "AGENTS.md", size: 12 },
    ],
    more_files: 0,
  });
  vi.mocked(pipelinesApi.file).mockResolvedValue({ path: "ihsDataR/R/steps.R", head: "abc1234def", size: 7, text: "x <- 1\n", too_large: false });
  vi.mocked(pipelinesApi.proposals).mockResolvedValue([proposal]);
});

it("groups the repo's files by folder, and knows each one's language", () => {
  const top = folderTree([
    { path: "workflows/weekly.yaml", size: 1 },
    { path: "ihsDataR/R/steps.R", size: 1 },
    { path: "AGENTS.md", size: 1 },
  ]);
  expect(top.folders.map((f) => f.name)).toEqual(["ihsDataR", "workflows"]);
  expect(top.folders[0].folders[0]).toMatchObject({ name: "R", path: "ihsDataR/R/", files: [{ name: "steps.R" }] });
  expect(top.files.map((f) => f.path)).toEqual(["AGENTS.md"]);
  expect([languageOf("a/b.R"), languageOf("w.yml"), languageOf("README.md"), languageOf("DESCRIPTION")]).toEqual([
    "r",
    "yaml",
    "markdown",
    "text",
  ]);
});

it("says where the repo stands, and how each change and its tests went", () => {
  expect(repoLine({ ...status, repo: "signed out" }).text).toMatch(/Sign in to GitHub \(Settings → GitHub\)/);
  expect(repoLine({ ...status, repo: "behind", behind: 2 }).text).toBe("GitHub has 2 newer commits: press Sync.");
  expect(proposalChip({ ...proposal, status: "tests_failed" })).toEqual({ text: "tests failed", tone: "bad" });
  expect(testLine(null).text).toBe("The package's tests haven't run on this change yet.");
  expect(testLine(passed)).toEqual({ text: "All 3 tests passed.", tone: "good" });
  expect(testLine({ ...passed, status: "failed", message: "2 of 5 tests failed." }).tone).toBe("bad");
});

it("browses the package read-only, lists the changes to review, and docks a Data engineering chat", async () => {
  sessionStorage.setItem("datalab:pipelines:chat-open", "open");
  render(
    <QueryClientProvider client={client()}>
      <PipelinesPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/In sync with GitHub/)).toBeInTheDocument();
  expect(screen.getByText("The engineering chat")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "steps.R" }));
  await waitFor(() => expect(pipelinesApi.file).toHaveBeenCalledWith("ihsDataR/R/steps.R"));
  expect(await screen.findByText(/Read-only: ask the agent to change it/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Changes (1)" }));
  const list = screen.getByRole("button", { name: /to review/ });
  expect(within(list).getByText("ihsDataR/R/steps.R")).toBeInTheDocument();
  expect(within(list).getByText(/Weekly steps/)).toBeInTheDocument();
});

it("asks the person to sign in when GitHub isn't signed in", async () => {
  vi.mocked(pipelinesApi.status).mockResolvedValue({ ...status, repo: "signed out", signed_in: false });
  render(
    <QueryClientProvider client={client()}>
      <PipelinesPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/Sign in to GitHub \(Settings → GitHub\)/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sync" })).toBeNull();
  expect(pipelinesApi.files).not.toHaveBeenCalled();
});

it("reviews a change: its diff, its tests, what wasn't proposed, and Save & share", async () => {
  vi.mocked(pipelinesApi.proposal).mockResolvedValue(detail());
  vi.mocked(pipelinesApi.accept).mockResolvedValue(detail({ status: "saving" }));
  render(
    <QueryClientProvider client={client()}>
      <ProposalView id="pp_1" onBack={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/Proposed by the agent in “Weekly steps”, after turn 2/)).toBeInTheDocument();
  expect(screen.getByText("The package's tests haven't run on this change yet.")).toBeInTheDocument();
  expect(screen.getByText(/only changes to ihsDataR\/ and workflows\/ are proposed/)).toBeInTheDocument();
  expect(screen.getByText(/Runs the tests first/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save & share" }));
  await waitFor(() => expect(pipelinesApi.accept).toHaveBeenCalledWith("pp_1", []));
  expect(await screen.findByText(/Saving: checking and sharing it/)).toBeInTheDocument();
});

it("won't save possible participant data until each line is confirmed", async () => {
  const finding = {
    id: "f1",
    path: "ihsDataR/tests/testthat/fixtures/steps.csv",
    rule: "study_id",
    severity: "data" as const,
    message: "This looks like a participant or study ID.",
    line: 2,
  };
  vi.mocked(pipelinesApi.proposal).mockResolvedValue(detail({ test: passed }, [finding]));
  vi.mocked(pipelinesApi.accept).mockResolvedValue(detail({ status: "saving", test: passed }, [finding]));
  render(
    <QueryClientProvider client={client()}>
      <ProposalView id="pp_1" onBack={vi.fn()} />
    </QueryClientProvider>,
  );
  const save = await screen.findByRole("button", { name: "Save & share" });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /steps.csv:2.*it isn't participant data/ }));
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() => expect(pipelinesApi.accept).toHaveBeenCalledWith("pp_1", ["f1"]));
});

it("says how each ending went: saved as a commit, or why nothing was shared", async () => {
  vi.mocked(pipelinesApi.proposal).mockResolvedValueOnce(
    detail({
      status: "saved",
      commit: "5eed5eed99",
      decided_by: "yfang",
      test: passed,
      result: { state: "saved", message: "Saved and shared with the lab.", findings: [], conflicts: [], after_rebase: false },
    }),
  );
  const view = render(
    <QueryClientProvider client={client()}>
      <ProposalView id="pp_1" onBack={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Saved and shared with the lab. It's commit 5eed5ee on main, saved by @yfang.",
  );
  expect(screen.queryByRole("button", { name: "Save & share" })).toBeNull();
  view.unmount();

  const message = "With the changes others saved meanwhile, the tests didn't pass: 1 of 4 tests failed.";
  vi.mocked(pipelinesApi.proposal).mockResolvedValueOnce(
    detail({
      status: "tests_failed",
      test: { ...passed, status: "failed", failed: 1, tests: 4, passed: 3, message: "1 of 4 tests failed.", failures: [{ file: "test-steps.R", test: "weekly", kind: "failure" }] },
      result: { state: "tests_failed", message, findings: [], conflicts: [], after_rebase: true },
    }),
  );
  render(
    <QueryClientProvider client={client()}>
      <ProposalView id="pp_1" onBack={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("status")).toHaveTextContent(message);
  expect(screen.getByText("failed: test-steps.R › weekly")).toBeInTheDocument();
  // Still the person's to decide: save again (tests first) or discard.
  expect(screen.getByRole("button", { name: "Save & share" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Discard" })).toBeEnabled();
});
