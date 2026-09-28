// The editor's public types. Nothing here imports CodeMirror, so a page can use
// them without pulling the editor into the main bundle.
import type { Extension } from "@codemirror/state";

export type EditorLanguage = "sql" | "yaml" | "markdown" | "r" | "python" | "shell" | "json" | "text";

/** A problem to mark in the text: 1-based line and column, as a checker reports them. */
export interface EditorDiagnostic {
  line: number;
  /** 1-based; the start of the line if not given. */
  column?: number;
  message: string;
  severity: "error" | "warning" | "info";
}

export interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  /** Plain text if not given. SQL is Oracle's (PL/SQL). */
  language?: EditorLanguage;
  readOnly?: boolean;
  /** Problems in `value` as it is now. They move with the text as it's edited, until the next list
   *  (a new array, so keep it stable between reports). */
  diagnostics?: EditorDiagnostic[];
  /** What the text is, for screen readers: "SQL query". */
  label: string;
  /** Ctrl+Enter (⌘+Enter on a Mac), such as running the query. */
  onSubmit?: () => void;
  /** Extra CodeMirror extensions, such as completion from the catalog. Keep this list stable (useMemo). */
  extensions?: Extension[];
  /** For the frame: its height, or how it grows. It is 6rem tall at least. */
  className?: string;
}

export interface DiffViewProps {
  original: string;
  modified: string;
  language?: EditorLanguage;
  /** What is being compared, for screen readers: "analysis.R". */
  label: string;
  /** Side by side (the default), or one column with removed lines shown above what replaced them. */
  layout?: "split" | "unified";
  className?: string;
}
