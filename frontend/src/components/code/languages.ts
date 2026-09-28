// The languages DataLab highlights, and how to tell a file's or a fenced block's
// language. Nothing here imports CodeMirror, so any page can use it without
// pulling a parser into the main bundle (the parsers load with highlight.ts).

export type CodeLanguage = "sql" | "r" | "python" | "shell" | "yaml" | "json" | "markdown" | "diff" | "text";

const ALIASES: Record<string, CodeLanguage> = {
  sql: "sql", plsql: "sql", oracle: "sql", duckdb: "sql", sqlite: "sql",
  r: "r", rscript: "r", rmd: "markdown", qmd: "markdown",
  python: "python", py: "python", python3: "python", ipython: "python",
  shell: "shell", sh: "shell", bash: "shell", zsh: "shell", console: "shell", shellsession: "shell",
  yaml: "yaml", yml: "yaml",
  json: "json", jsonc: "json", json5: "json", "vega-lite": "json",
  markdown: "markdown", md: "markdown",
  diff: "diff", patch: "diff",
  text: "text", txt: "text", plaintext: "text",
}; // prettier-ignore

/** A fenced block's language (```r, ```bash…), or "text" for one DataLab doesn't know. */
export function codeLanguage(name: string | null | undefined): CodeLanguage {
  return ALIASES[(name ?? "").trim().toLowerCase()] ?? "text";
}

const EXTENSIONS: Record<string, CodeLanguage> = {
  r: "r", py: "python", sql: "sql", sh: "shell", bash: "shell", zsh: "shell",
  yaml: "yaml", yml: "yaml", json: "json", md: "markdown", qmd: "markdown", rmd: "markdown",
  diff: "diff", patch: "diff",
}; // prettier-ignore

/** A file's language, by its extension. */
export function languageOfPath(path: string): CodeLanguage {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSIONS[extension] ?? "text";
}

// The code files the Code tab lists (mirrors the backend's sessions/code.py).
// Only types the file viewer also shows as text (backend api/files.py _TEXT).
const CODE = new Set(["r", "py", "sql", "ipynb", "qmd", "rmd", "sh", "yaml", "yml"]);

/** Whether a file is code (a script, SQL, a notebook…): the Code tab's, not Outputs'. */
export function isCodeFile(path: string): boolean {
  return CODE.has(path.split(".").pop()?.toLowerCase() ?? "");
}

/** A short label for a language, as the Code tab shows it. */
export function languageLabel(language: string): string {
  switch (language) {
    case "r":
      return "R";
    case "python":
      return "Python";
    case "sql":
      return "SQL";
    case "shell":
      return "Shell";
    case "yaml":
      return "YAML";
    case "json":
      return "JSON";
    case "markdown":
      return "Markdown";
    case "notebook":
      return "Notebook";
    default:
      return "Code";
  }
}

/** Past this, code is shown as plain text: highlighting it would hold the page up. */
export const MAX_HIGHLIGHT_CHARS = 200_000;
