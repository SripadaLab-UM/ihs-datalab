// A unified diff, highlighted: each line with its numbers on both sides, a
// sign, and its code highlighted as the version it comes from has it.
import clsx from "clsx";

import { Line, useHighlight } from "./CodeBlock";
import type { Token } from "./highlight";
import type { CodeLanguage } from "./languages";

export interface DiffLine {
  op: " " | "+" | "-" | "@";
  old: number | null;
  new: number | null;
  text: string;
}

export function CodeDiff({
  lines,
  baseText,
  headText,
  language,
  label,
}: {
  lines: DiffLine[];
  baseText: string;
  headText: string;
  language: CodeLanguage;
  label: string;
}) {
  // Each side highlighted whole, so a line inside a long string or comment reads right.
  const base = useHighlight(baseText, language);
  const head = useHighlight(headText, language);
  const tokens = (line: DiffLine): Token[] => {
    const side = line.op === "-" ? base : head;
    const number = line.op === "-" ? line.old : line.new;
    const marked = side && number != null ? side[number - 1] : undefined;
    // Only if it's the same line (a file with odd line breaks may number them differently).
    if (marked && marked.map((t) => t.text).join("").replace(/\r$/, "") === line.text) return marked;
    return [{ text: line.text, className: "" }];
  };
  return (
    <div role="table" aria-label={label} className="dl-code overflow-auto bg-sunken py-2 font-mono text-[12.5px] leading-[1.6] text-ink">
      {lines.map((line, i) =>
        line.op === "@" ? (
          <div key={i} role="row" className="mt-1 bg-field px-3 py-0.5 font-sans text-[12px] text-muted first:mt-0">
            <span role="cell">{hunkLabel(line.text)}</span>
          </div>
        ) : (
          <div
            key={i}
            role="row"
            data-op={line.op === "+" ? "added" : line.op === "-" ? "removed" : "same"}
            className={clsx("flex whitespace-pre", line.op === "+" && "bg-data-soft", line.op === "-" && "bg-danger-soft")}
          >
            <span role="cell" aria-hidden className="w-[3em] shrink-0 pr-2 text-right text-faint select-none">
              {line.old ?? ""}
            </span>
            <span role="cell" aria-hidden className="w-[3em] shrink-0 pr-2 text-right text-faint select-none">
              {line.new ?? ""}
            </span>
            <span
              role="cell"
              className={clsx("w-[1.4em] shrink-0 text-center select-none", line.op === "+" ? "text-data" : line.op === "-" ? "text-danger" : "text-faint")}
            >
              <span className="sr-only">{line.op === "+" ? "added" : line.op === "-" ? "removed" : ""}</span>
              <span aria-hidden>{line.op === "+" ? "+" : line.op === "-" ? "−" : ""}</span>
            </span>
            <span role="cell" className="min-w-0 flex-1 pr-3">
              <Line tokens={tokens(line)} />
            </span>
          </div>
        ),
      )}
    </div>
  );
}

/** "@@ -3,7 +3,8 @@" → "Lines 3–9 → 3–10". */
function hunkLabel(header: string): string {
  const match = /^@@ -(\d+),(\d+) \+(\d+),(\d+) @@/.exec(header);
  if (!match) return header;
  const [a, n, b, m] = match.slice(1).map(Number);
  const span = (start: number, count: number) => (count <= 1 ? `${start}` : `${start}–${start + count - 1}`);
  return `Lines ${span(a, n)} → ${span(b, m)}`;
}
