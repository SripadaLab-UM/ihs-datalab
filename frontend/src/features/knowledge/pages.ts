// The knowledge base's files, grouped the way the Knowledge tab lists them, and
// links between pages. Pure, so it's tested without a browser.
import type { KbEntry } from "@/api/knowledge";

// The layout's folders (docs/KNOWLEDGE_BASE.md, backend knowledge/check.py), in order.
const FOLDERS: [string, string][] = [
  ["sources", "Data sources"],
  ["tables", "Tables and views"],
  ["features", "Derived features"],
  ["qc", "QC rules"],
  ["cohorts", "Cohorts"],
  ["queries", "Verified queries"],
  ["decisions", "Decisions"],
  ["papers", "Papers"],
];

export interface PageGroup {
  key: string;
  title: string;
  entries: KbEntry[];
}

/** The files by folder: the top files, each page folder, the lab skills, generated/. Empty groups are left out. */
export function groupPages(entries: KbEntry[], filter = ""): PageGroup[] {
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = entries.filter((e) => {
    const haystack = `${e.path} ${e.title} ${e.summary}`.toLowerCase();
    return words.every((w) => haystack.includes(w));
  });
  const byTitle = (a: KbEntry, b: KbEntry) => a.title.localeCompare(b.title);
  const groups: PageGroup[] = [
    { key: "top", title: "About", entries: shown.filter((e) => e.place === "top") },
    ...FOLDERS.map(([folder, title]) => ({
      key: folder,
      title,
      entries: shown.filter((e) => e.place === "page" && e.path.startsWith(`${folder}/`)).sort(byTitle),
    })),
    {
      key: "skills",
      title: "Lab skills",
      // Each skill's SKILL.md, then the files beside it.
      entries: shown
        .filter((e) => e.place === "skill" || e.place === "skill_file")
        .sort((a, b) => skillOrder(a).localeCompare(skillOrder(b))),
    },
    { key: "generated", title: "Generated", entries: shown.filter((e) => e.place === "generated") },
  ];
  return groups.filter((g) => g.entries.length > 0);
}

function skillOrder(entry: KbEntry): string {
  const [, name] = entry.path.split("/");
  return `${name}\0${entry.place === "skill" ? "" : entry.path}`;
}

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
