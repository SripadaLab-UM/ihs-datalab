// Below 2xl the shortcuts are icons only, so the header fits: whatever needs
// attention is said here instead, in one button. With one thing, the button
// says it ("Key missing"); with more, how many ("3 need attention"). Its menu
// lists each, in words, linking to where it's sorted out. From 2xl up each
// shortcut says its own, and this isn't shown.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "@/api/client";
import { githubApi } from "@/api/github";
import { Icon } from "@/components/ui";
import { type HighlightId, settingsLink } from "@/features/settings/highlight";
import type { SettingsSectionId } from "@/features/settings/sectionIds";
import { useUpdateCheck } from "@/features/settings/updateCheck";

import { databaseStanding, keyStanding, useConnections, useConnectionTest } from "./ConnectionShortcuts";
import { useFolders } from "./FoldersShortcut";
import { ITEM, Shortcut } from "./Popover";

export interface AttentionItem {
  id: string;
  /** The words, as the shortcut would say them ("Key missing"). */
  text: string;
  /** What to do about it. */
  hint: string;
  section: SettingsSectionId;
  anchor?: HighlightId;
}

/** Everything in the toolbar that needs attention now, most pressing first. */
export function useAttention(): AttentionItem[] {
  const connections = useConnections().data;
  const test = useConnectionTest().result;
  const { unavailable } = useFolders();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const real = health.data !== undefined && health.data.profile !== "practice";
  const githubQuery = useQuery({ queryKey: ["github-status"], queryFn: githubApi.status, enabled: real });
  const github = githubQuery.data;
  const update = useUpdateCheck().data;
  const items: AttentionItem[] = [];
  const db = databaseStanding(connections, test?.database);
  if (db.attention) {
    items.push({ id: "database", text: db.attention, hint: "The study database: open connection settings", section: "connections", anchor: "connection-database" });
  }
  const key = keyStanding(connections, test?.model);
  if (key.attention) {
    items.push({ id: "key", text: key.attention, hint: "U-M GPT key: open connection settings", section: "connections", anchor: "connection-umgpt" });
  }
  // Once GitHub's status is known, so the hint doesn't change under the pointer.
  if (real && health.data?.catalog_state === "empty" && !githubQuery.isPending) {
    // Every query names columns the check looks up in it: none runs without one.
    items.push(
      github?.available
        ? { id: "catalog", text: "No table catalog", hint: "No query can run: sign in to GitHub and sync the knowledge base", section: "connections", anchor: "connection-github" }
        : { id: "catalog", text: "No table catalog", hint: "No query can run: see why, and ask the DataLab maintainer", section: "about" },
    );
  }
  if (real && github?.available && !github.signed_in) {
    items.push({ id: "github", text: "GitHub: sign in", hint: "The lab's knowledge base and pipelines can't sync", section: "connections", anchor: "connection-github" });
  }
  if (unavailable) {
    items.push({ id: "folders", text: "Folder unavailable", hint: `${unavailable === 1 ? "An export folder isn't" : `${unavailable} export folders aren't`} available now`, section: "export-folders", anchor: "export-folders" });
  }
  if (update?.state === "available" && update.available) {
    items.push({ id: "update", text: "Update available", hint: `DataLab ${update.available.version}: see what's new`, section: "updates" });
  }
  return items;
}

export function attentionLabel(items: AttentionItem[]): string {
  return items.length === 1 ? items[0].text : `${items.length} need attention`;
}

export function AttentionMenu() {
  const items = useAttention();
  if (items.length === 0) return null;
  const label = attentionLabel(items);
  return (
    <Shortcut
      kind="menu"
      icon="alert"
      label={`Needs attention: ${items.map((i) => i.text).join(", ")}`}
      title={`${label}: ${items.map((i) => i.text).join(" · ")} · click for each`}
      text={label}
      tone="attn"
      width="w-72"
      className="2xl:hidden"
    >
      {(close) =>
        items.map((item) => (
          <Link
            key={item.id}
            role="menuitem"
            tabIndex={-1}
            {...settingsLink(item.section, item.anchor)}
            onClick={() => close(false)}
            className={ITEM}
          >
            <Icon name="alert" size={14} className="shrink-0 self-start text-attn" />
            <span className="min-w-0">
              <span className="block font-medium text-attn">{item.text}</span>
              <span className="block text-[12px] text-muted">{item.hint}</span>
            </span>
          </Link>
        ))
      }
    </Shortcut>
  );
}
