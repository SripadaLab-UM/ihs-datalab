import { Link } from "react-router";

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
      <Link to="/settings#updates" className={`${PILL} border-attn/50 text-attn`} role="status">
        Updating to {check.install.version}…
      </Link>
    );
  }
  if (check.state !== "available" || !check.available) return null;
  return (
    <Link
      to="/settings#updates"
      className={`${PILL} border-you/50 bg-you-soft text-you hover:border-you`}
      title={`DataLab ${check.available.version} is available (this is ${check.current_version}). See what's new in Settings.`}
    >
      Update available
    </Link>
  );
}
