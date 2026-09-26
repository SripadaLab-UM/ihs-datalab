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
