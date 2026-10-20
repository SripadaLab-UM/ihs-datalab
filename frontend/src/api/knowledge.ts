// Calls for the Knowledge tab. Add this tab's calls here: it's the only API file
// its work needs to touch.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type KnowledgeStatus = Schemas["KnowledgeStatus"];
export type RepoState = KnowledgeStatus["repo"];
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
export type KbEdit = Schemas["EditOut"];
export type KbEditState = KbEdit["status"];
export type KbEditSummary = Schemas["EditSummaryOut"];
export type KbEditCheck = Schemas["EditCheckOut"];
export type KbReapply = Schemas["ReapplyOut"];
export type KbSuggestionOut = Schemas["SuggestionOut"];

const post = (body?: unknown): RequestInit => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const id = encodeURIComponent;

export const knowledgeApi = {
  /** Whether the knowledge base is set up, who is signed in, and where the local copy is. */
  status: () => request<KnowledgeStatus>("/api/knowledge/status"),
  /** Download GitHub's main (a clone the first time). */
  sync: () => request<KnowledgeStatus>("/api/knowledge/sync", post()),

  // Signing in to GitHub is github.ts: one sign-in for both lab repos.

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

  // A person's own edits (the Edit page): drafts kept on this computer until Save & share.
  /** The edits not yet shared or discarded. */
  edits: () => request<KbEditSummary[]>("/api/knowledge/edits"),
  /** Start editing a page (or a new one), or find the page's edit already open. */
  startEdit: (path: string, isNew = false) => request<KbEdit>("/api/knowledge/edits", post({ path, new: isNew })),
  getEdit: (editId: string) => request<KbEdit>(`/api/knowledge/edits/${id(editId)}`),
  /** Keep as a draft on this computer. `version` is the draft's updated_at as last read. */
  keepEdit: (editId: string, text: string, version: string) =>
    request<KbEdit>(`/api/knowledge/edits/${id(editId)}`, { method: "PUT", body: JSON.stringify({ text, version }) }),
  /** The check, as Save & share runs it, on this text (nothing is kept). */
  checkEdit: (editId: string, text: string) => request<KbEditCheck>(`/api/knowledge/edits/${id(editId)}/check`, post({ text })),
  /** Move the draft onto GitHub's newer version: merged, or with `resolution` as the text. */
  reapplyEdit: (editId: string, version: string, resolution?: string) =>
    request<KbReapply>(`/api/knowledge/edits/${id(editId)}/reapply`, post({ version, resolution: resolution ?? null })),
  /** Save & share: what the person saw (the draft's text_sha256, the findings' ids). */
  shareEdit: (editId: string, confirmed: string[], seen: string, findings: string[]) =>
    request<KbEdit>(`/api/knowledge/edits/${id(editId)}/share`, post({ confirmed, seen, findings })),
  discardEdit: (editId: string) => request<KbEdit>(`/api/knowledge/edits/${id(editId)}/discard`, post()),

  // Suggested Knowledge updates from a conversation.
  acceptSuggestion: (conversationId: string, suggestionId: string) =>
    request<KbEdit>(`/api/knowledge/suggestions/${id(conversationId)}/${id(suggestionId)}/accept`, post()),
  dismissSuggestion: (conversationId: string, suggestionId: string) =>
    request<KbSuggestionOut>(`/api/knowledge/suggestions/${id(conversationId)}/${id(suggestionId)}/dismiss`, post()),
  // Remember in Knowledge is a message to the conversation's agent: api.send(…, kbRequest).
};

/** A commit on GitHub, for a repo named "owner/name". */
export function commitUrl(repo: string | null | undefined, commit: string): string | null {
  return repo && /^[\w.-]+\/[\w.-]+$/.test(repo) && /^[0-9a-f]{7,40}$/.test(commit)
    ? `https://github.com/${repo}/commit/${commit}`
    : null;
}
