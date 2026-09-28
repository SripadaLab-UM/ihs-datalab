import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Link } from "react-router";

import { api } from "@/api/client";
import { githubApi } from "@/api/github";

import { GITHUB_ANCHOR, settingsPath } from "./sectionIds";

/** GitHub's mark (Primer Octicons, MIT), so the entry is recognisable at a glance. */
export function GitHubMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" className="shrink-0">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

/**
 * The shell's GitHub entry: who is signed in, or "Sign in", opening Settings →
 * Connections → GitHub. Nothing on practice (it never signs in to GitHub; the
 * practice badge says which DataLab this is) or where the lab's repos aren't set up.
 */
export function GitHubEntry() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const real = health.data !== undefined && health.data.profile !== "practice";
  // The same query as Settings → Connections, so signing in there shows here at once.
  const status = useQuery({ queryKey: ["github-status"], queryFn: githubApi.status, enabled: real });
  const github = status.data;
  if (!real || !github?.available) return null;
  const login = github.account?.login;
  const signedIn = github.signed_in && login;
  return (
    <Link
      to={settingsPath("connections", GITHUB_ANCHOR)}
      aria-label={signedIn ? `GitHub: signed in as ${login}` : "Sign in to GitHub"}
      title={
        signedIn
          ? `Signed in to GitHub as ${login}${github.account?.name ? ` (${github.account.name})` : ""}. See the lab repos' access in Settings → Connections.`
          : "Not signed in to GitHub: the lab's knowledge base and pipelines can't sync. Sign in in Settings → Connections."
      }
      className={clsx(
        "flex shrink-0 items-center gap-1.5 self-center rounded-[3px] px-1.5 py-1 font-sans text-[13px] leading-none whitespace-nowrap transition-colors",
        signedIn ? "text-muted hover:text-ink" : "text-attn hover:text-ink",
      )}
    >
      <GitHubMark />
      {/* Signed in, only the mark (as the other shortcuts, which add words only
          when something needs attention); the tooltip names the login. */}
      {signedIn ? (
        <span className="sr-only">{login}</span>
      ) : (
        <span className="hidden xl:inline">Sign in</span>
      )}
    </Link>
  );
}
