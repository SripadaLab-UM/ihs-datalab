// The shortcuts at the right of the header, beside Help. From 2xl up:
// Database, U-M GPT key, GitHub and Export folders, then More (⋯) and the
// session menu. Below 2xl, GitHub and Export folders move into More, so the
// header never overflows. "Update available" shows only when it's relevant.
import { GitHubEntry } from "@/features/settings/GitHubEntry";
import { useDeployment } from "@/lib/deployment";

import { DatabaseShortcut, KeyShortcut } from "./ConnectionShortcuts";
import { FoldersShortcut } from "./FoldersShortcut";

/** Where the wide window's extra shortcuts show (More holds them below it). */
export const WIDE_ONLY = "hidden 2xl:flex";

export function Shortcuts() {
  const { server, admin } = useDeployment();
  // The lab's server: its connections are its administrators' to look after;
  // everyone is signed in with GitHub already, and exports are downloads.
  if (server) {
    return admin ? (
      <div className="flex shrink-0 items-center gap-0.5 self-stretch" role="group" aria-label="Connections">
        <DatabaseShortcut />
        <KeyShortcut />
      </div>
    ) : null;
  }
  return (
    <div className="flex shrink-0 items-center gap-0.5 self-stretch" role="group" aria-label="Connections and folders">
      <DatabaseShortcut />
      <KeyShortcut />
      <div className={`${WIDE_ONLY} shrink-0 items-center gap-0.5 self-stretch`}>
        <GitHubEntry />
        <FoldersShortcut />
      </div>
    </div>
  );
}
