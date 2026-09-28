// Links between the knowledge base's pages, and how a page's status reads
// (the list's grouping is tree.ts). Pure, so it's tested without a browser.
import type { KbEntry } from "@/api/knowledge";

/** What a page's link points to, as a path in the knowledge base, or null if it leaves it. */
export function resolveLink(from: string, href: string): string | null {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//") || href.startsWith("#")) return null;
  const target = href.split("#")[0].split("?")[0];
  if (!target) return null;
  const parts = target.startsWith("/") ? [] : from.split("/").slice(0, -1);
  for (const part of target.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) return null;
      parts.pop();
    } else parts.push(part);
  }
  return parts.length ? parts.join("/") : null;
}

/** A `related` entry ("qc/midnight-sleep") or a link, as one of the files listed, if it is one. */
export function findPage(entries: KbEntry[], path: string): KbEntry | undefined {
  return entries.find((e) => e.path === path) ?? entries.find((e) => e.path === `${path}.md`);
}

/** How a page's status reads, and its colour. */
export function statusTone(status: string | null | undefined): "good" | "attn" | "bad" | undefined {
  if (status === "reviewed") return "good";
  if (status === "draft") return "attn";
  if (status === "deprecated") return "bad";
  return undefined;
}
