// The check for a newer DataLab release, shared by the shell's pill and Settings → Updates.
// DataLab's host asks GitHub (at start, and on "Check now"); the page only reads the answer.
import { useQuery } from "@tanstack/react-query";

import { settingsApi, type UpdateCheck } from "@/api/settings";

export const UPDATE_CHECK = ["update-check"];

const WORKING = new Set([
  "downloading",
  "stopping",
  "backing-up",
  "installing",
  "pulling-images",
  "switching",
  "restarting",
]);

/** Whether an update is being installed (or DataLab is restarting into it). */
export function installing(check: UpdateCheck | undefined): boolean {
  return !!check && WORKING.has(check.install.state);
}

export function useUpdateCheck() {
  return useQuery({
    queryKey: UPDATE_CHECK,
    queryFn: settingsApi.updateCheck,
    // Often while installing, to show each step; otherwise now and then, since the
    // check at start finishes in the background.
    refetchInterval: (query) => (installing(query.state.data) ? 2000 : 5 * 60_000),
    retry: false,
  });
}
