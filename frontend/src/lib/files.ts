// Workspace paths as the agent writes them in answers, and what kind of file each is.
import { createContext } from "react";

import type { FileRoot } from "@/api/client";

export type FileKind = "html" | "image" | "csv" | "text" | "pdf" | "other";

export interface OpenFile {
  root: FileRoot;
  path: string;
  kind: FileKind;
  size?: number;
  /** The checkpoint it was listed from; the viewer pins the latest if not given. */
  checkpoint?: number | null;
}

const TEXT = new Set([
  "txt", "md", "json", "log", "py", "r", "sql", "yaml", "yml", "toml",
  "xml", "qmd", "rmd", "ipynb", "sh", "tex", "bib", "svg", "css",
]);

/** Mirrors the backend's `kind_of` (api/files.py). */
export function kindOf(path: string): FileKind {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  if (extension === "html" || extension === "htm") return "html";
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(extension)) return "image";
  if (extension === "csv" || extension === "tsv") return "csv";
  if (extension === "pdf") return "pdf";
  return TEXT.has(extension) ? "text" : "other";
}

/** A link in an answer to a file in the container, or null if it points elsewhere. */
export function workspaceFile(href: string): OpenFile | null {
  let path: string;
  try {
    // "/work/outputs/report.html:22" points at a line: open the file.
    path = decodeURI(href.split("#")[0].split("?")[0]).replace(/:\d+(?::\d+)?$/, "");
  } catch {
    return null;
  }
  const match = /^\/(work\/outputs|work|data\/oracle)\/(.+)$/.exec(path);
  if (!match || match[2].split("/").some((part) => part === ".." || part === "." || part === "")) return null;
  const root: FileRoot = match[1] === "work/outputs" ? "outputs" : match[1] === "work" ? "work" : "results";
  return { root, path: match[2], kind: kindOf(match[2]) };
}

/** Lets answers open workspace files in the viewer. Null outside the Workspace. */
export const OpenFileContext = createContext<((file: OpenFile) => void) | null>(null);

/** Where a file is in the container, as the agent writes it: the inverse of `workspaceFile`. */
export function containerPath(file: Pick<OpenFile, "root" | "path">): string {
  const base = file.root === "outputs" ? "/work/outputs" : file.root === "work" ? "/work" : "/data/oracle";
  return `${base}/${file.path}`;
}

/**
 * The start several file names share, cut at a separator, when it's long
 * enough to crowd out the part that tells them apart (sleep_mood_2025_pilot_).
 */
export function sharedPrefix(names: string[]): string {
  if (names.length < 2) return "";
  let prefix = names[0];
  for (const name of names.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < name.length && prefix[i] === name[i]) i++;
    prefix = prefix.slice(0, i);
  }
  const cut = Math.max(prefix.lastIndexOf("_"), prefix.lastIndexOf("-"), prefix.lastIndexOf(" "));
  prefix = cut >= 0 ? prefix.slice(0, cut + 1) : "";
  // Never swallow a whole name: every file keeps something to show.
  return prefix.length >= 8 && names.every((name) => name.length > prefix.length + 2) ? prefix : "";
}

/** What a file is, in a word a scientist would use, from its type and where it is. */
export function fileNoun(file: Pick<OpenFile, "root" | "path" | "kind">): string {
  const extension = file.path.split(".").pop()?.toLowerCase() ?? "";
  if (file.root === "results") return "query results (CSV)";
  if (file.kind === "html") return "report";
  if (file.kind === "pdf") return "report (PDF)";
  if (file.kind === "image" || extension === "svg") return "plot";
  if (file.kind === "csv") return extension === "tsv" ? "table (TSV)" : "table (CSV)";
  if (extension === "r") return "R script";
  if (extension === "py") return "Python script";
  if (extension === "sql") return "SQL file";
  if (["qmd", "rmd", "ipynb"].includes(extension)) return "notebook";
  if (extension === "md" || extension === "txt") return "notes";
  if (extension === "json") return "data (JSON)";
  if (extension === "log") return "log";
  return "file";
}

/** A file an answer names, as a button: what to call it and what to open. */
export interface AnswerFileLink {
  file: OpenFile;
  /** Where it is in the container, for the tooltip and the details. */
  path: string;
  label: string;
}

// Inline code and link targets, outside fenced blocks: where an answer names files.
const FENCED = /^(```|~~~)[^\n]*\n[\s\S]*?^\1[ \t]*$/gm;
const NAMED = /`([^`\n]+)`|\]\(([^)\s]+)\)/g;

/**
 * The files an answer names that this conversation is known to have, each
 * with a readable label: "Open report", or "Open table: flow (CSV)" when the
 * answer names more than one table. Anything not in `known` (container paths,
 * as `containerPath` writes them) isn't linked, so a link can only open a
 * file of this conversation in DataLab's own viewer.
 */
export function answerFileLinks(text: string, known: ReadonlySet<string>): Map<string, AnswerFileLink> {
  const files = new Map<string, OpenFile>();
  for (const match of text.replace(FENCED, "").matchAll(NAMED)) {
    const raw = (match[1] ?? match[2] ?? "").trim();
    if (!/^\/[^\s*?]+[^/]$/.test(raw)) continue;
    const file = workspaceFile(raw);
    if (!file) continue;
    const path = containerPath(file);
    if (known.has(path) && !files.has(path)) files.set(path, file);
  }
  const stem = (file: OpenFile) => {
    const name = file.path.split("/").at(-1) ?? file.path;
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(0, dot) : name;
  };
  // Files of one kind are told apart by name, without the start they share.
  const stems = new Map<string, string[]>();
  for (const file of files.values()) stems.set(fileNoun(file), [...(stems.get(fileNoun(file)) ?? []), stem(file)]);
  const links = new Map<string, AnswerFileLink>();
  for (const [path, file] of files) {
    const noun = fileNoun(file);
    const same = stems.get(noun) ?? [];
    let label = `Open ${noun}`;
    if (same.length > 1) {
      const prefix = sharedPrefix(same);
      const short = stem(file).slice(prefix.length).replace(/[_-]+/g, " ").trim() || stem(file);
      // "table (CSV)" → "table: flow (CSV)"
      const format = /^(.*?)( \([^)]*\))?$/.exec(noun);
      label = `Open ${format?.[1] ?? noun}: ${short}${format?.[2] ?? ""}`;
    }
    links.set(path, { file, path, label });
  }
  return links;
}

/** Container paths of the files this conversation has (see `answerFileLinks`). Null: not known here. */
export const KnownFilesContext = createContext<ReadonlySet<string> | null>(null);

/** A query id as the agent writes it (q_20260926T194554_b2e2dd), which answers don't show. */
export const QUERY_ID = /^q_\d{8}T\d{6}_[0-9a-f]{6}$/i;
