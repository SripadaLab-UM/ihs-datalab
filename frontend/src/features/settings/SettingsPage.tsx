import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { type KeyboardEvent, useEffect, useRef } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router";

import { api } from "@/api/client";

import {
  DEFAULT_SECTION,
  isSection,
  rememberedSection,
  rememberSection,
  SETTINGS_SECTIONS,
  type SettingsSectionId,
  sectionForAnchor,
  settingsPath,
} from "./sectionIds";
import { AboutSection } from "./sections/AboutSection";
import { AppearanceSection } from "./sections/AppearanceSection";
import { ConnectionsSection } from "./sections/ConnectionsSection";
import { DestinationKeysSection } from "./sections/DestinationKeysSection";
import { DiagnosticsSection } from "./sections/DiagnosticsSection";
import { ExportDestinations } from "./sections/ExportDestinations";
import { SafetySection, safetySummary } from "./sections/SafetySection";
import { StorageSection } from "./sections/StorageSection";
import { UpdatesSection } from "./sections/UpdatesSection";
import { OLD_ANCHORS, useSettingsHighlight } from "./highlight";
import { useUpdateCheck } from "./updateCheck";

function anchorOf(hash: string): string | null {
  if (!hash) return null;
  try {
    return decodeURIComponent(hash.slice(1));
  } catch {
    return null; // a malformed link: stay at the top
  }
}

/**
 * Settings & Safety: a list of sections on the left (tabs along the top on a
 * narrow window), and the one chosen. Each has its own address
 * (/settings/updates); /settings opens the one last used, else Connections.
 */
export function SettingsPage() {
  const { section } = useParams();
  const { hash } = useLocation();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  const current = isSection(section) ? section : null;
  const anchor = anchorOf(hash);

  useEffect(() => {
    if (current) rememberSection(current);
  }, [current]);
  // A link from elsewhere in DataLab lights up the part it's about (highlight.ts).
  const { announcement, handledHash } = useSettingsHighlight();
  // A place inside a section (/settings/export-folders#destination-keys); an
  // old anchor (#github) finds its part's new id. Not the place a highlight
  // just scrolled to: that scroll (centred, smooth) stands.
  useEffect(() => {
    if (!current || !anchor || anchor === current || handledHash.current === hash) return;
    document.getElementById(OLD_ANCHORS[anchor] ?? anchor)?.scrollIntoView?.({ block: "start" });
  }, [current, anchor, hash, health.isSuccess, handledHash]);

  if (!current) {
    // An old link (/settings#updates) goes to its section; else the last one used.
    const target = (anchor && sectionForAnchor(anchor)) || rememberedSection() || DEFAULT_SECTION;
    const place = anchor && anchor !== target ? `#${encodeURIComponent(anchor)}` : "";
    return <Navigate to={`${settingsPath(target)}${place}`} replace />;
  }

  let panel;
  switch (current) {
    case "connections":
      panel = <ConnectionsSection />;
      break;
    case "appearance":
      panel = <AppearanceSection />;
      break;
    case "export-folders":
      panel = (
        <>
          <ExportDestinations practice={practice} />
          {health.data && <DestinationKeysSection practice={practice} />}
        </>
      );
      break;
    case "updates":
      panel = <UpdatesSection />;
      break;
    case "storage":
      panel = <StorageSection />;
      break;
    case "safety":
      panel = <SafetySection />;
      break;
    case "about":
      panel = (
        <>
          <AboutSection health={health.data} />
          <DiagnosticsSection />
        </>
      );
      break;
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 sm:px-8 md:flex-row md:gap-12 md:py-12">
        <h1 className="sr-only">Settings &amp; Safety</h1>
        <p aria-live="polite" className="sr-only" data-testid="settings-announcement">
          {announcement}
        </p>
        <SectionNav current={current} />
        <div className="flex min-w-0 max-w-3xl flex-1 flex-col gap-14">{panel}</div>
      </div>
    </div>
  );
}

/** The sections: a column of links on a wide window, a row of tabs on a narrow one. Arrow keys move between them. */
function SectionNav({ current }: { current: SettingsSectionId }) {
  const list = useRef<HTMLUListElement>(null);
  const safety = useQuery({ queryKey: ["safety"], queryFn: api.lastSafetyReport });
  const update = useUpdateCheck().data;
  const summary = safetySummary(safety.data);
  const marks: Partial<Record<SettingsSectionId, { tone: "good" | "attn" | "bad" | "you"; label: string }>> = {};
  if (summary.tone !== "none") marks.safety = { tone: summary.tone, label: summary.text };
  if (update?.state === "available" && update.available) marks.updates = { tone: "you", label: "Update available" };

  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const links = [...(list.current?.querySelectorAll<HTMLAnchorElement>("a") ?? [])];
    const at = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (at < 0) return;
    const next = {
      ArrowDown: at + 1,
      ArrowRight: at + 1,
      ArrowUp: at - 1,
      ArrowLeft: at - 1,
      Home: 0,
      End: links.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    links[(next + links.length) % links.length]?.focus();
  };

  return (
    <nav aria-label="Settings sections" className="shrink-0 md:sticky md:top-0 md:w-44 md:self-start">
      <ul
        ref={list}
        onKeyDown={onKeyDown}
        className="-mx-1 flex gap-1 overflow-x-auto border-b border-line md:mx-0 md:flex-col md:gap-0.5 md:overflow-visible md:border-b-0"
      >
        {SETTINGS_SECTIONS.map((s) => {
          const active = s.id === current;
          const mark = marks[s.id];
          return (
            <li key={s.id} className="shrink-0">
              <Link
                to={settingsPath(s.id)}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "flex items-center gap-2 font-sans text-[13.5px] whitespace-nowrap transition-colors focus-visible:outline-offset-[-3px]",
                  "border-b-2 px-2.5 py-2 md:rounded-[3px] md:border-b-0 md:border-l-2 md:px-3 md:py-1.5",
                  active ? "border-ink font-medium text-ink md:bg-sunken" : "border-transparent text-muted hover:text-ink",
                )}
              >
                {s.label}
                {mark && (
                  <span
                    className={clsx(
                      "size-[7px] shrink-0 rounded-full",
                      mark.tone === "good" && "bg-data",
                      mark.tone === "attn" && "bg-attn",
                      mark.tone === "bad" && "bg-danger",
                      mark.tone === "you" && "bg-you",
                    )}
                    role="img"
                    aria-label={mark.label}
                    title={mark.label}
                  />
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
