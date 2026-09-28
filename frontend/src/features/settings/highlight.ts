// Links into Settings from elsewhere (the toolbar's shortcuts) say which part
// they're about, as react-router state { highlight: "<element id>" }:
//   <Link {...settingsLink("connections", "connection-database")}>
// A minimal version: the b6-cursor branch brings the full one (Settings
// scrolls to that part and lights it up); keep its version on merge.
import { type SettingsSectionId, settingsPath } from "./sectionIds";

export type HighlightId =
  | "connection-umgpt"
  | "connection-database"
  | "connection-github"
  | "appearance"
  | "export-folders"
  | "destination-keys"
  | "updates-check"
  | "diagnostics";

export interface SettingsHighlightState {
  highlight: string;
}

/** Where a link into Settings goes, and what it lights up there: spread it on a <Link>. */
export function settingsLink(
  section: SettingsSectionId,
  anchorId?: HighlightId,
): { to: string; state?: SettingsHighlightState } {
  return anchorId ? { to: settingsPath(section, anchorId), state: { highlight: anchorId } } : { to: settingsPath(section) };
}
