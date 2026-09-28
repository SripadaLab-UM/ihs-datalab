// "More" (⋯): the less-used shortcuts. Appearance and Feedback always; on a
// narrow window also Export folders and GitHub, which have their own buttons
// from 2xl up (see Toolbar).
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import { githubApi } from "@/api/github";
import { Icon, Modal } from "@/components/ui";
import { GitHubMark } from "@/features/settings/GitHubEntry";
import { settingsLink } from "@/features/settings/highlight";
import { type Theme, useTheme } from "@/lib/theme";

import { EMPTY_DRAFT, FeedbackDialog, type FeedbackDraft } from "./FeedbackDialog";
import { FoldersPanel } from "./FoldersShortcut";
import { ITEM, Shortcut } from "./Popover";

const THEMES: { value: Theme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/** Where the narrow window's extra items show: below 2xl, where their own buttons are hidden. */
export const NARROW_ONLY = "2xl:hidden";

function useGitHub() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const real = health.data !== undefined && health.data.profile !== "practice";
  const status = useQuery({ queryKey: ["github-status"], queryFn: githubApi.status, enabled: real });
  const github = status.data;
  if (!real || !github?.available) return null;
  const login = github.signed_in ? github.account?.login : undefined;
  return { login };
}

export function MoreMenu() {
  const { theme, setTheme } = useTheme();
  const github = useGitHub();
  const [dialog, setDialog] = useState<"feedback" | "folders" | null>(null);
  // Kept while the page is open: closing Feedback and coming back finds it as it was.
  const [draft, setDraft] = useState<FeedbackDraft>(EMPTY_DRAFT);
  return (
    <>
      <Shortcut kind="menu" icon="more" label="More" title="More: appearance, and report a bug or suggest an improvement" width="w-60">
        {(close) => (
          <>
            <div role="none" className={NARROW_ONLY}>
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={ITEM}
                onClick={() => {
                  close();
                  setDialog("folders");
                }}
              >
                <Icon name="folder" size={14} className="text-muted" /> Export folders…
              </button>
              {github && (
                <Link
                  role="menuitem"
                  tabIndex={-1}
                  {...settingsLink("connections", "connection-github")}
                  onClick={() => close(false)}
                  className={ITEM}
                >
                  <span className="text-muted">
                    <GitHubMark size={14} />
                  </span>
                  {github.login ? (
                    <span className="min-w-0 truncate">
                      GitHub: <span className="font-mono text-[12.5px]">{github.login}</span>
                    </span>
                  ) : (
                    <span className="text-attn">GitHub: sign in</span>
                  )}
                </Link>
              )}
              <div role="separator" className="my-1 border-t border-line" />
            </div>
            <div role="group" aria-labelledby="more-appearance">
              <p id="more-appearance" role="presentation" className="dl-label flex items-center gap-1.5 px-3 pt-1.5 pb-1">
                <Icon name="sun" size={12} /> Appearance
              </p>
              {THEMES.map((choice) => {
                const chosen = theme === choice.value;
                return (
                  <button
                    key={choice.value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={chosen}
                    tabIndex={-1}
                    className={clsx(ITEM, chosen && "font-medium")}
                    onClick={() => setTheme(choice.value)}
                  >
                    <span className="inline-flex w-3.5 justify-center" aria-hidden="true">
                      {chosen ? <Icon name="check" size={13} /> : null}
                    </span>
                    {choice.label}
                  </button>
                );
              })}
            </div>
            <div role="separator" className="my-1 border-t border-line" />
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={ITEM}
              onClick={() => {
                close();
                setDialog("feedback");
              }}
            >
              <Icon name="message" size={14} className="text-muted" /> Send feedback…
            </button>
          </>
        )}
      </Shortcut>
      {dialog === "feedback" && <FeedbackDialog draft={draft} onDraft={setDraft} onClose={() => setDialog(null)} />}
      {dialog === "folders" && (
        <Modal title="Export folders" onClose={() => setDialog(null)}>
          <FoldersPanel onNavigate={() => setDialog(null)} />
        </Modal>
      )}
    </>
  );
}
