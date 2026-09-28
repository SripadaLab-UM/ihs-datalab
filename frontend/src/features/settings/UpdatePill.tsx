import { Link } from "react-router";

import { settingsPath } from "./sectionIds";
import { installing, useUpdateCheck } from "./updateCheck";

const PILL =
  "shrink-0 self-center rounded-full border px-2.5 py-1 font-sans text-[12.5px] leading-none font-medium whitespace-nowrap transition-colors";

/**
 * "Update available", in the shell's header, when the last check found a newer
 * release. It opens Settings → Updates, which has the release notes and
 * Install update (always confirmed there). Nothing shows when DataLab is up to
 * date, offline, or couldn't check.
 */
export function UpdatePill() {
  const check = useUpdateCheck().data;
  if (!check) return null;
  if (installing(check)) {
    return (
      <Link to={settingsPath("updates")} className={`${PILL} border-attn/50 text-attn hover:border-attn`} role="status">
        Updating to {check.install.version}…
      </Link>
    );
  }
  if (check.state !== "available" || !check.available) return null;
  return (
    <Link
      to={settingsPath("updates")}
      className={`${PILL} border-you/50 bg-you-soft text-you hover:border-you`}
      title={`DataLab ${check.available.version} is available (this is ${check.current_version}). See what's new in Settings.`}
    >
      Update available
    </Link>
  );
}

/**
 * While an update is being installed, DataLab refuses anything that would start
 * new work (a turn, a query, an export, a sync...), so every page says why.
 */
export function UpdatingBanner() {
  const check = useUpdateCheck().data;
  if (!check?.updating) return null;
  return (
    <div role="status" className="bg-attn px-4 py-2 text-center text-sm font-medium text-white">
      DataLab is being updated to {check.install.version ?? "a new version"}. Nothing new can start until it has
      restarted; it opens again in a new window.
    </div>
  );
}
