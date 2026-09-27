// What the Pipelines tab says about the repo, a proposal, and its tests. Kept
// apart from the components so the wording can be checked on its own.
import type { PipelineProposal, PipelinesStatus, PipelineTest } from "@/api/pipelines";
import type { EditorLanguage } from "@/components/editor/CodeEditor";

export function languageOf(path: string): EditorLanguage {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  if (extension === "r") return "r";
  if (extension === "yaml" || extension === "yml") return "yaml";
  if (extension === "md" || extension === "rmd" || extension === "qmd") return "markdown";
  if (extension === "sql") return "sql";
  return "text";
}

export interface Folder {
  name: string;
  path: string; // with a trailing "/", "" for the top
  folders: Folder[];
  files: { name: string; path: string; size: number }[];
}

/** The repo's files as folders, each sorted: folders first, then files. */
export function folderTree(files: { path: string; size: number }[]): Folder {
  const top: Folder = { name: "", path: "", folders: [], files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let at = top;
    for (const part of parts.slice(0, -1)) {
      let next = at.folders.find((f) => f.name === part);
      if (!next) {
        next = { name: part, path: `${at.path}${part}/`, folders: [], files: [] };
        at.folders.push(next);
      }
      at = next;
    }
    at.files.push({ name: parts[parts.length - 1], path: file.path, size: file.size });
  }
  const sort = (folder: Folder) => {
    folder.folders.sort((a, b) => a.name.localeCompare(b.name));
    folder.files.sort((a, b) => a.name.localeCompare(b.name));
    folder.folders.forEach(sort);
  };
  sort(top);
  return top;
}

/** What the repo bar says, and whether the files can be browsed. */
export function repoLine(status: PipelinesStatus | undefined): { text: string; tone: "muted" | "attn" | "bad" } {
  if (!status) return { text: "Looking for the pipelines repo…", tone: "muted" };
  switch (status.repo) {
    case "not configured":
      return { text: status.message ?? "The pipelines repo isn't set up.", tone: "muted" };
    case "signed out":
      return { text: "Sign in to GitHub (Settings → GitHub) to use the pipelines repo.", tone: "attn" };
    case "no access":
    case "sync failed":
      return { text: status.message ?? "DataLab couldn't sync the pipelines repo.", tone: "bad" };
    case "not cloned":
      return { text: "Not downloaded yet: press Sync.", tone: "attn" };
    case "behind":
      return { text: `GitHub has ${status.behind} newer commit${status.behind === 1 ? "" : "s"}: press Sync.`, tone: "attn" };
    case "diverged":
      return { text: "This copy has commits GitHub doesn't. Ask the DataLab maintainer.", tone: "bad" };
    case "in sync":
      return { text: `In sync with GitHub${status.last_sync ? `, ${when(status.last_sync)}` : ""}.`, tone: "muted" };
  }
}

export function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

type Tone = "good" | "attn" | "bad" | "you" | undefined;

/** A proposal's state in a word or two, for its chip. */
export function proposalChip(proposal: PipelineProposal): { text: string; tone: Tone } {
  switch (proposal.status) {
    case "open":
      return { text: "to review", tone: "you" };
    case "saving":
      return { text: "saving", tone: undefined };
    case "saved":
      return { text: "saved", tone: "good" };
    case "conflict":
      return { text: "conflict", tone: "bad" };
    case "check_failed":
      return { text: "check", tone: "attn" };
    case "tests_failed":
      return { text: "tests failed", tone: "bad" };
    case "failed":
      return { text: "not saved", tone: "bad" };
    case "rejected":
      return { text: "discarded", tone: undefined };
    case "superseded":
      return { text: "replaced", tone: undefined };
    case "withdrawn":
      return { text: "undone", tone: undefined };
  }
}

export const ACTIONABLE = new Set(["open", "conflict", "check_failed", "tests_failed", "failed"]);

/** The tests in a line: how they went on this change, or that they haven't run. */
export function testLine(test: PipelineTest | null | undefined): { text: string; tone: Tone } {
  if (!test) return { text: "The package's tests haven't run on this change yet.", tone: undefined };
  switch (test.status) {
    case "running":
      return { text: "The package's tests are running…", tone: undefined };
    case "passed":
      return {
        text: `All ${test.tests} tests passed${test.skipped ? ` (${test.skipped} skipped)` : ""}.`,
        tone: "good",
      };
    case "failed":
      return { text: test.message ?? `${test.failed + test.errors} of ${test.tests} tests failed.`, tone: "bad" };
    case "error":
      return { text: test.message ?? "The tests couldn't run.", tone: "bad" };
  }
}

export function busy(proposal: PipelineProposal): boolean {
  return proposal.status === "saving" || proposal.test?.status === "running";
}
