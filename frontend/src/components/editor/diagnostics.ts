import type { Diagnostic } from "@codemirror/lint";
import type { Text } from "@codemirror/state";

import type { EditorDiagnostic } from "./types";

/** Line-and-column problems as CodeMirror ranges: from the column to the end of the word there.
 *  A line or column past the end is taken as the last one, so a stale report still shows. */
export function toRanges(doc: Text, diagnostics: EditorDiagnostic[]): Diagnostic[] {
  return diagnostics.map((d) => {
    const line = doc.line(Math.min(Math.max(1, Math.floor(d.line) || 1), doc.lines));
    const from = line.from + Math.min(Math.max(0, (d.column ?? 1) - 1), line.length);
    const word = /^[\w$#]+/.exec(line.text.slice(from - line.from));
    // A point where there's no word (the end of a line, a space): one character, if there is one.
    const to = word ? from + word[0].length : Math.min(from + 1, line.to);
    return { from, to, severity: d.severity, message: d.message };
  });
}
