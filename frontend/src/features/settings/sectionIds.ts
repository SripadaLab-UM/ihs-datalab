// Settings' sections, each at its own address (/settings/updates), so the
// toolbar, other screens and Help can link straight to one.

export const SETTINGS_SECTIONS = [
  { id: "account", label: "Account" },
  { id: "connections", label: "Connections" },
  { id: "appearance", label: "Appearance" },
  { id: "export-folders", label: "Export folders" },
  { id: "updates", label: "Updates" },
  { id: "storage", label: "Storage" },
  { id: "safety", label: "Safety" },
  { id: "about", label: "About" },
] as const;

export type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]["id"];

export const DEFAULT_SECTION: SettingsSectionId = "connections";

/**
 * The sections someone sees. On a person's computer, all but Account (it's
 * theirs alone). On the lab's shared DataLab (a server): their Account and
 * Appearance, and About; administrators also look after the lab's
 * Connections, Storage and Safety. Export folders and Updates are a
 * computer's: the server downloads exports, and its administrator updates it.
 */
export function visibleSections(server: boolean, admin: boolean): SettingsSectionId[] {
  if (!server) return SETTINGS_SECTIONS.map((s) => s.id).filter((id) => id !== "account");
  return admin
    ? ["account", "connections", "appearance", "storage", "safety", "about"]
    : ["account", "appearance", "about"];
}

/** GitHub's place in Connections, where the toolbar's GitHub entry links. */
export const GITHUB_ANCHOR = "connection-github";

/** The processed data's place in Connections, where the new-conversation dialog links. */
export const PROCESSED_ANCHOR = "connection-processed";

/** Where the last section opened is remembered, in this browser only. */
export const LAST_SECTION_KEY = "datalab.settings.section";

export function isSection(id: string | undefined): id is SettingsSectionId {
  return SETTINGS_SECTIONS.some((s) => s.id === id);
}

/** A section's address, with a place in it: settingsPath("export-folders", "destination-keys"). */
export function settingsPath(section: SettingsSectionId, anchor?: string): string {
  return `/settings/${section}${anchor ? `#${anchor}` : ""}`;
}

// Links made before sections had addresses (`/settings#updates`), and the
// places inside a section, mapped to their section.
const ANCHORS: Record<string, SettingsSectionId> = {
  "destination-keys": "export-folders",
  github: "connections",
  "um-gpt": "connections",
  database: "connections",
  "connection-github": "connections",
  "connection-umgpt": "connections",
  "connection-database": "connections",
  "connection-processed": "connections",
  "updates-check": "updates",
  diagnostics: "about",
};

export function sectionForAnchor(anchor: string): SettingsSectionId | undefined {
  if (isSection(anchor)) return anchor;
  return ANCHORS[anchor];
}

export function rememberedSection(): SettingsSectionId | undefined {
  try {
    const saved = localStorage.getItem(LAST_SECTION_KEY) ?? undefined;
    return isSection(saved) ? saved : undefined;
  } catch {
    return undefined;
  }
}

export function rememberSection(section: SettingsSectionId) {
  try {
    localStorage.setItem(LAST_SECTION_KEY, section);
  } catch {
    // Storage refused: Settings opens on Connections next time.
  }
}
