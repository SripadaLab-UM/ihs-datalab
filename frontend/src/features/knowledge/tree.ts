// The Knowledge tab's list as a tree: sections, the groups in them, and the
// pages. Built from the knowledge base's own layout (docs/KNOWLEDGE_BASE.md,
// backend knowledge/check.py) and each page's front matter. Pure, so it's
// tested without a browser.
import type { KbEntry } from "@/api/knowledge";

/** What one part of a section takes from the knowledge base. */
export type SectionPart =
  // The pages in one of the layout's page folders. With `tables`, each page
  // (a source) gets the pages of that folder linked to it by `related`, either
  // way, beneath it; the tables linked to no source go in `unlinked`.
  | {
      folder: string;
      title?: string;
      tables?: { folder: string; unlinked: string };
    }
  // The top files (index.md, README.md, AGENTS.md), or generated/'s reports.
  | { place: "top" | "generated"; title?: string }
  // The lab skills, each SKILL.md with the files beside it beneath it.
  | { skills: true; title?: string };

export interface SectionConfig {
  key: string;
  title: string;
  parts: SectionPart[];
}

/**
 * The sections of the Knowledge tab's list, in order: the one place to change
 * how the list is grouped. Each part with a `title` is a group of its own in
 * the section; one without is listed straight in the section. A section with
 * nothing in it is left out, and every file no part takes is listed under
 * "Other", so nothing is ever hidden by this list.
 */
export const SECTIONS: SectionConfig[] = [
  {
    key: "study",
    title: "Study documentation",
    parts: [
      { place: "top" },
      { folder: "cohorts", title: "Cohorts" },
      { folder: "decisions", title: "Decisions" },
      { folder: "papers", title: "Lab papers" },
    ],
  },
  {
    key: "sources",
    title: "Data sources",
    parts: [
      {
        folder: "sources",
        tables: { folder: "tables", unlinked: "Tables without a source" },
      },
      { place: "generated", title: "Catalog reports" },
    ],
  },
  {
    key: "methods",
    title: "Analysis methods",
    parts: [
      { folder: "features", title: "Derived features" },
      { folder: "queries", title: "Verified queries" },
    ],
  },
  { key: "qc", title: "Quality checks", parts: [{ folder: "qc" }] },
  {
    key: "workflows",
    title: "Workflows and pipelines",
    parts: [{ skills: true }],
  },
];

// The top files, in reading order; any other top file comes after them.
const TOP_ORDER = ["index.md", "README.md", "AGENTS.md"];

export interface TreeNode {
  /** Stable across syncs: `section:…`, `group:…`, or `page:<path>`. */
  id: string;
  label: string;
  /** The file it opens, for a page; a page can have pages beneath it (a source's tables, a skill's files). */
  entry?: KbEntry;
  children: TreeNode[];
  /** How many pages are beneath it. */
  count: number;
}

const pageId = (path: string) => `page:${path}`;
const byTitle = (a: KbEntry, b: KbEntry) => a.title.localeCompare(b.title) || a.path.localeCompare(b.path);
const folderOf = (entry: KbEntry) => (entry.path.includes("/") ? entry.path.split("/")[0] : "");
const noMd = (path: string) => path.replace(/\.md$/, "");

function node(id: string, label: string, children: TreeNode[], entry?: KbEntry): TreeNode {
  return {
    id,
    label,
    entry,
    children,
    count: children.reduce((n, c) => n + (c.entry ? 1 : 0) + c.count, 0),
  };
}

function pageNode(entry: KbEntry, children: TreeNode[] = []): TreeNode {
  const label = entry.place === "skill_file" ? entry.path.split("/").slice(2).join("/") : entry.title;
  return node(pageId(entry.path), label, children, entry);
}

/** Tables by their cohort schema (IHS_2025.X), when they span more than one; else as they are. */
function bySchema(parent: string, tables: KbEntry[]): TreeNode[] {
  const schemas = new Map<string, KbEntry[]>();
  for (const t of tables) {
    const schema = t.title.includes(".") ? t.title.split(".")[0] : "";
    schemas.set(schema, [...(schemas.get(schema) ?? []), t]);
  }
  if (schemas.size < 2) return tables.map((t) => pageNode(t));
  return [...schemas.entries()]
    .sort(([a], [b]) => b.localeCompare(a)) // newest cohort first, as in the Playground
    .map(([schema, list]) =>
      schema
        ? node(
            `${parent}/${schema}`,
            schema,
            list.map((t) => pageNode(t)),
          )
        : list.map((t) => pageNode(t)),
    )
    .flat();
}

/** The knowledge base's files as the tree the list shows, by `sections` (SECTIONS unless a test gives others). */
export function buildTree(entries: KbEntry[], sections: SectionConfig[] = SECTIONS): TreeNode[] {
  const taken = new Set<string>();
  const take = (list: KbEntry[]) => {
    const fresh = list.filter((e) => !taken.has(e.path));
    for (const e of fresh) taken.add(e.path);
    return fresh;
  };
  const inFolder = (folder: string) => entries.filter((e) => e.place === "page" && folderOf(e) === folder);

  const partNodes = (section: string, part: SectionPart): TreeNode[] => {
    if ("skills" in part) {
      const skills = take(entries.filter((e) => e.place === "skill")).sort(byTitle);
      return skills.map((skill) => {
        const folder = skill.path.split("/").slice(0, 2).join("/") + "/";
        const files = take(entries.filter((e) => e.place === "skill_file" && e.path.startsWith(folder))).sort((a, b) => a.path.localeCompare(b.path));
        return pageNode(
          skill,
          files.map((f) => pageNode(f)),
        );
      });
    }
    if ("place" in part) {
      const list = take(entries.filter((e) => e.place === part.place));
      if (part.place === "top") {
        const rank = (e: KbEntry) => (TOP_ORDER.includes(e.path) ? TOP_ORDER.indexOf(e.path) : TOP_ORDER.length);
        list.sort((a, b) => rank(a) - rank(b) || a.path.localeCompare(b.path));
      } else list.sort(byTitle);
      return list.map((e) => pageNode(e));
    }
    const pages = take(inFolder(part.folder)).sort(byTitle);
    if (!part.tables) return pages.map((e) => pageNode(e));
    const { folder, unlinked } = part.tables;
    const tables = inFolder(folder)
      .filter((e) => !taken.has(e.path))
      .sort(byTitle);
    const linked = new Map<string, KbEntry[]>();
    for (const table of tables) {
      // A table under the first source it links to, or that links to it.
      const source = pages.find((s) => table.related.some((r) => noMd(r) === noMd(s.path)) || s.related.some((r) => noMd(r) === noMd(table.path)));
      if (source) linked.set(source.path, [...(linked.get(source.path) ?? []), table]);
    }
    const nodes = pages.map((source) => {
      const mine = take(linked.get(source.path) ?? []);
      return pageNode(source, bySchema(pageId(source.path), mine));
    });
    const rest = take(tables);
    if (rest.length) nodes.push(node(`group:${section}/${folder}`, unlinked, bySchema(`group:${section}/${folder}`, rest)));
    return nodes;
  };

  const tree: TreeNode[] = sections.map((section) => {
    const children = section.parts.flatMap((part) => {
      const nodes = partNodes(section.key, part);
      if (!part.title || nodes.length === 0) return nodes;
      const key = "folder" in part ? part.folder : "place" in part ? part.place : "skills";
      return [node(`group:${section.key}/${key}`, part.title, nodes)];
    });
    return node(`section:${section.key}`, section.title, children);
  });

  // Whatever no section took, by folder: nothing is hidden.
  const left = entries.filter((e) => !taken.has(e.path));
  if (left.length) {
    const folders = new Map<string, KbEntry[]>();
    for (const e of left) folders.set(folderOf(e), [...(folders.get(folderOf(e)) ?? []), e]);
    const children = [...folders.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .flatMap(([folder, list]) => {
        const pages = list.sort((a, b) => a.path.localeCompare(b.path)).map((e) => pageNode(e));
        return folder ? [node(`group:other/${folder}`, `${folder}/`, pages)] : pages;
      });
    tree.push(node("section:other", "Other", children));
  }
  return tree.filter((s) => s.count > 0);
}

/** Does a page match every word typed? Its path, title, summary, kind, status and cohorts count. */
export function matches(entry: KbEntry, words: string[]): boolean {
  const haystack = [entry.path, entry.title, entry.summary, entry.kind, entry.status, ...entry.cohorts].join(" ").toLowerCase();
  return words.every((w) => haystack.includes(w));
}

export const searchWords = (text: string) => text.toLowerCase().split(/\s+/).filter(Boolean);

export interface Filtered {
  tree: TreeNode[];
  /** The pages that match (the others are shown only to place them). */
  hits: Set<string>;
  /** Every node with a match beneath it: what search opens. */
  open: Set<string>;
}

/** The tree cut down to the pages that match and what holds them, with counts of the matches. */
export function filterTree(tree: TreeNode[], words: string[]): Filtered {
  const hits = new Set<string>();
  const open = new Set<string>();
  const walk = (n: TreeNode): TreeNode | null => {
    const children = n.children.map(walk).filter((c): c is TreeNode => c !== null);
    const hit = n.entry ? matches(n.entry, words) : false;
    if (hit) hits.add(n.id);
    if (!hit && children.length === 0) return null;
    if (children.length) open.add(n.id);
    return {
      ...n,
      children,
      count: children.reduce((k, c) => k + (hits.has(c.id) ? 1 : 0) + c.count, 0),
    };
  };
  return {
    tree: tree.map(walk).filter((n): n is TreeNode => n !== null),
    hits,
    open,
  };
}

/** The ids of the nodes above the page at `path`, outermost first (empty if it isn't in the tree). */
export function ancestorsOf(tree: TreeNode[], path: string): string[] {
  const target = pageId(path);
  const walk = (nodes: TreeNode[], above: string[]): string[] | null => {
    for (const n of nodes) {
      if (n.id === target) return above;
      const found = walk(n.children, [...above, n.id]);
      if (found) return found;
    }
    return null;
  };
  return walk(tree, []) ?? [];
}

export interface Visible {
  node: TreeNode;
  level: number;
  parent: string | null;
}

/** The rows shown, top to bottom, given which nodes are open: what the arrow keys move through. */
export function visibleRows(tree: TreeNode[], isOpen: (id: string) => boolean): Visible[] {
  const rows: Visible[] = [];
  const walk = (nodes: TreeNode[], level: number, parent: string | null) => {
    for (const n of nodes) {
      rows.push({ node: n, level, parent });
      if (n.children.length && isOpen(n.id)) walk(n.children, level + 1, n.id);
    }
  };
  walk(tree, 1, null);
  return rows;
}

export { pageId };
