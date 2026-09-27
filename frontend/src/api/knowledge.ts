// Calls for the Knowledge tab. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type KnowledgeStatus = Schemas["KnowledgeStatus"];
export type RepoState = KnowledgeStatus["repo"];
export type SignIn = Schemas["SignInOut"];
export type Proposal = Schemas["ProposalOut"];
export type ProposalState = Proposal["status"];
export type ProposalDetail = Schemas["ProposalDetail"];
export type ProposalFile = Schemas["FileDetail"];
export type Finding = Schemas["FindingOut"];
export type SaveResult = Schemas["SaveResultOut"];
export type KbEntry = Schemas["KbEntryOut"];
export type KbPages = Schemas["KbPagesOut"];
export type KbPage = Schemas["KbPageOut"];
export type KbCommit = Schemas["KbCommitOut"];

const post = (body?: unknown): RequestInit => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const id = encodeURIComponent;

export const knowledgeApi = {
  /** Whether the knowledge base is set up, who is signed in, and where the local copy is. */
  status: () => request<KnowledgeStatus>("/api/knowledge/status"),
  /** Download GitHub's main (a clone the first time). */
  sync: () => request<KnowledgeStatus>("/api/knowledge/sync", post()),

  /** Where signing in to GitHub is, without asking GitHub. */
  signIn: () => request<SignIn>("/api/knowledge/sign-in"),
  /** Start GitHub's device flow: the code to enter at github.com/login/device. */
  startSignIn: () => request<SignIn>("/api/knowledge/sign-in", post()),
  /** Ask whether the code was entered; call it every `interval` seconds. */
  pollSignIn: () => request<SignIn>("/api/knowledge/sign-in/poll", post()),
  cancelSignIn: () => request<SignIn>("/api/knowledge/sign-in/cancel", post()),
  signOut: () => request<SignIn>("/api/knowledge/sign-out", post()),

  proposals: (conversationId?: string) =>
    request<Proposal[]>(`/api/knowledge/proposals${conversationId ? `?conversation_id=${id(conversationId)}` : ""}`),
  /** A proposal with each file's texts, and the check as Save & share would run it. */
  proposal: (proposalId: string) => request<ProposalDetail>(`/api/knowledge/proposals/${id(proposalId)}`),
  /** The person's own text for some files (null leaves a file out); `reset` goes back to the agent's. */
  edit: (proposalId: string, files: Record<string, string | null>, reset: string[] = []) =>
    request<ProposalDetail>(`/api/knowledge/proposals/${id(proposalId)}/edits`, {
      method: "PUT",
      body: JSON.stringify({ files, reset }),
    }),
  /**
   * Save & share, with the ids of the data findings the person confirmed aren't
   * participant data, and what they saw: each file's `after_sha256` and the ids
   * of the check's findings. DataLab refuses (409) if either changed since.
   */
  accept: (proposalId: string, confirmed: string[], seen: Record<string, string | null>, findings: string[]) =>
    request<ProposalDetail>(`/api/knowledge/proposals/${id(proposalId)}/accept`, post({ confirmed, seen, findings })),
  /** Discard: nothing is shared. */
  reject: (proposalId: string) => request<ProposalDetail>(`/api/knowledge/proposals/${id(proposalId)}/reject`, post()),

  /** The pages, lab skills and top files of GitHub's main as last synced. */
  pages: () => request<KbPages>("/api/knowledge/pages"),
  page: (path: string) => request<KbPage>(`/api/knowledge/pages/${path.split("/").map(id).join("/")}`),
  /** The latest commits of main as last synced, newest first. */
  history: (limit = 20) => request<KbCommit[]>(`/api/knowledge/history?limit=${limit}`),
};

/** A commit on GitHub, for a repo named "owner/name". */
export function commitUrl(repo: string | null | undefined, commit: string): string | null {
  return repo && /^[\w.-]+\/[\w.-]+$/.test(repo) && /^[0-9a-f]{7,40}$/.test(commit)
    ? `https://github.com/${repo}/commit/${commit}`
    : null;
}
