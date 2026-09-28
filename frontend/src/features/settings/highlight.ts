// Links into Settings from elsewhere (the toolbar's Update pill and GitHub
// entry, "Add one in Settings" on an export) say which part they're about.
// Settings scrolls to that part, moves focus to its heading, and lights it up
// briefly, so the eye lands where the link meant. Opening Settings itself, or
// its own section list, highlights nothing.
//
// The convention, for any link into Settings:
//   <Link {...settingsLink("updates", "updates-check")}>
// navigates with react-router state { highlight: "<element id>" }. A deep link
// without that state (a bookmark, a hash typed in) still scrolls, but doesn't
// highlight.
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { type SettingsSectionId, settingsPath } from "./sectionIds";

/** The parts of Settings a link can point at: their element ids, and what they're called when announced. */
export const HIGHLIGHT_TARGETS = {
  "connection-umgpt": { section: "connections", name: "Connections, U-M GPT" },
  "connection-database": { section: "connections", name: "Connections, the study database" },
  "connection-github": { section: "connections", name: "Connections, GitHub" },
  appearance: { section: "appearance", name: "Appearance" },
  "export-folders": { section: "export-folders", name: "Export folders" },
  "destination-keys": { section: "export-folders", name: "Export folders, Workflow destinations" },
  "updates-check": { section: "updates", name: "Updates, Check now" },
  diagnostics: { section: "about", name: "About, Diagnostics" },
} as const satisfies Record<string, { section: SettingsSectionId; name: string }>;

export type HighlightId = keyof typeof HIGHLIGHT_TARGETS;

/** Anchors from before these ids (`#github`), still accepted in a link's hash. */
export const OLD_ANCHORS: Record<string, HighlightId> = {
  github: "connection-github",
  "um-gpt": "connection-umgpt",
  database: "connection-database",
};

export interface SettingsHighlightState {
  highlight: string;
}

/**
 * Where a link into Settings goes, and what it lights up there: spread it on
 * a <Link>. The hash keeps the place in the address, so the link still works
 * copied or bookmarked (without the highlight).
 */
export function settingsLink(section: SettingsSectionId, anchorId?: HighlightId): { to: string; state?: SettingsHighlightState } {
  return anchorId ? { to: settingsPath(section, anchorId), state: { highlight: anchorId } } : { to: settingsPath(section) };
}

/** How long the highlight stays: it fades over this, or holds still under reduced motion. */
export const HIGHLIGHT_MS = 1800;

function reducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    return false;
  }
}

/** The heading to move focus to: the part's own, else its section's; the part itself if neither. */
function headingOf(target: HTMLElement): HTMLElement {
  const own = target.matches("h1, h2, h3, h4") ? target : target.querySelector<HTMLElement>("h2, h3, h4");
  const heading = own ?? target.closest("section")?.querySelector<HTMLElement>("h2, h3") ?? target;
  if (!heading.hasAttribute("tabindex") && !heading.matches("a[href], button, input, select, textarea")) heading.tabIndex = -1;
  return heading;
}

/**
 * Scroll to the part with this id, focus its heading and light it up for
 * HIGHLIGHT_MS. The part may still be loading, or be drawn again once its data
 * arrives (a new element with the same id): it waits up to 5 s for the first,
 * and moves the highlight to the new one. It runs on its own, apart from
 * React, so the page dropping the navigation's state doesn't cut it short.
 * Returns what ends it early (the page going away). For a part lit up from
 * the same page (a folder just added, say), `focus: false` leaves focus where
 * the person is still reading what they did, and `block: "nearest"` scrolls
 * only as far as it takes to show the part.
 */
export function lightUp(
  id: string,
  onFound: () => void,
  { focus = true, block = "center" }: { focus?: boolean; block?: ScrollLogicalPosition } = {},
): () => void {
  const still = reducedMotion();
  const className = still ? "dl-highlight-still" : "dl-highlight";
  let current: HTMLElement | null = null;
  // Focus moves only if the person hasn't put it somewhere since the link was followed.
  const before = document.activeElement;
  const mayFocus = () => {
    const now = document.activeElement;
    return !now || now === document.body || now === before;
  };
  const mark = (target: HTMLElement) => {
    const first = current === null;
    current = target;
    target.scrollIntoView?.({ block, behavior: still || !first ? "auto" : "smooth" });
    // Focus goes to the part's heading (again if the part is drawn again), unless the person has moved it.
    if (focus && mayFocus()) headingOf(target).focus({ preventScroll: true });
    // Restarted if the same part is asked for again.
    target.classList.remove("dl-highlight", "dl-highlight-still");
    void target.offsetWidth;
    target.classList.add(className);
    if (first) {
      onFound();
      setTimeout(finish, HIGHLIGHT_MS);
    }
  };
  const check = () => {
    const target = document.getElementById(id);
    if (target && target !== current) mark(target);
  };
  const observer = new MutationObserver(check);
  const giveUp = setTimeout(() => current === null && observer.disconnect(), 5000);
  function finish() {
    observer.disconnect();
    clearTimeout(giveUp);
    current?.classList.remove(className);
  }
  observer.observe(document.body, { childList: true, subtree: true });
  check();
  return finish;
}

/** What useSettingsHighlight gives the page. */
export interface SettingsHighlight {
  /** For a live region: "Settings: Updates, Check now", cleared once the highlight ends so a repeat is heard. */
  announcement: string;
  /** The hash of the navigation it handled: the page's own scroll to a hash leaves that one alone. */
  handledHash: { readonly current: string | null };
}

/**
 * On Settings: if the navigation asked to highlight a part, light it up
 * (lightUp), once.
 */
export function useSettingsHighlight(): SettingsHighlight {
  const location = useLocation();
  const navigate = useNavigate();
  const asked = (location.state as Partial<SettingsHighlightState> | null)?.highlight;
  const id = typeof asked === "string" ? (OLD_ANCHORS[asked] ?? asked) : undefined;
  const [announcement, setAnnouncement] = useState("");
  const stop = useRef<() => void>(undefined);
  const handledHash = useRef<string | null>(null);
  useEffect(() => () => stop.current?.(), []);

  useEffect(() => {
    if (!id) return;
    stop.current?.();
    handledHash.current = location.hash;
    stop.current = lightUp(id, () => {
      const name = id in HIGHLIGHT_TARGETS ? HIGHLIGHT_TARGETS[id as HighlightId].name : "";
      setAnnouncement(name ? `Settings: ${name}` : "");
      setTimeout(() => setAnnouncement(""), HIGHLIGHT_MS);
    });
    // Once: going back to this entry, or reloading, doesn't light it up again.
    navigate(`${location.pathname}${location.search}${location.hash}`, { replace: true, state: null });
    // Each navigation that asks is its own highlight (location.key), even to the same part.
  }, [id, location.key]);

  return { announcement, handledHash };
}
