// Where the knowledge base's copy on this computer is, in plain words: shared by
// the Knowledge tab and Settings → GitHub. Pure, so it's tested without a browser.
import type { KnowledgeStatus } from "@/api/knowledge";

export interface RepoStateView {
  label: string;
  tone?: "good" | "attn" | "bad" | "you";
  text: string;
  /** Sync can help (or is how to start). */
  canSync: boolean;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** What repoState reads: the knowledge base's status, or the pipelines repo's (the same states). */
export type RepoStatusLike = Pick<KnowledgeStatus, "repo" | "message" | "ahead" | "behind" | "last_error">;

export function repoState(status: RepoStatusLike): RepoStateView {
  const message = status.message ?? "";
  switch (status.repo) {
    case "not configured":
      return { label: "not set up", text: message || "The knowledge base isn't set up on this DataLab.", canSync: false };
    case "signed out":
      return { label: "signed out", tone: "you", text: message || "Sign in to GitHub to use it.", canSync: false };
    case "no access":
      return { label: "no access", tone: "attn", text: message, canSync: true };
    case "not cloned":
      return { label: "not downloaded", tone: "you", text: "It isn't on this computer yet. Press Sync to download it.", canSync: true };
    case "in sync":
      return { label: "up to date", tone: "good", text: "Up to date with GitHub as of the last sync.", canSync: true };
    case "behind":
      return {
        label: `${status.behind} behind`,
        tone: "attn",
        text: `GitHub has ${plural(status.behind, "newer commit")} than this computer's copy. Press Sync to bring them in.`,
        canSync: true,
      };
    case "diverged":
      return {
        label: "diverged",
        tone: "bad",
        text: `This computer's copy has ${plural(status.ahead, "commit")} GitHub doesn't (and GitHub ${plural(status.behind, "commit")} it doesn't). DataLab never overwrites either: ask the DataLab maintainer.`,
        canSync: true,
      };
    case "sync failed":
      return { label: "sync failed", tone: "bad", text: message || status.last_error || "The last sync failed.", canSync: true };
  }
}

/** "5 minutes ago", for a time in ISO format. */
export function ago(iso: string, now = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${plural(minutes, "minute")} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${plural(hours, "hour")} ago`;
  const days = Math.round(hours / 24);
  if (days < 45) return `${plural(days, "day")} ago`;
  return new Date(then).toLocaleDateString();
}

/** GitHub's device page, only if that's what GitHub named (it's shown as a link). */
export function devicePage(uri: string | null | undefined): string {
  return uri && /^https:\/\/github\.com\/login\/device\/?$/.test(uri) ? uri : "https://github.com/login/device";
}
