// Provenance: where each number in an answer appears, and how an output file
// was made (backend sessions/provenance.py). The wording is DataLab's to keep
// honest: a number "appears in" a command's output or a query's result, not
// "was computed by" it; a file was written "by one of these commands" when no
// single one names it. Nothing here carries query rows or command output.

/** Where a number appears: a command (by its event id), a query (by its Data accessed id), or an output file. */
export interface NumberSource {
  kind: "command" | "query" | "file";
  ref: string;
}

/** The `provenance` event for one answer. */
export interface AnswerProvenance {
  answer: string | null;
  numbers: { text: string; sources: NumberSource[] }[];
  more_numbers: number;
  files: string[];
}

export type { FileProvenance } from "@/api/provenance";

/** A provenance event's data, or undefined if it isn't one. */
export function asAnswerProvenance(value: unknown): AnswerProvenance | undefined {
  if (!value || typeof value !== "object") return undefined;
  const numbers = (value as AnswerProvenance).numbers;
  if (!Array.isArray(numbers)) return undefined;
  return {
    answer: typeof (value as AnswerProvenance).answer === "string" ? (value as AnswerProvenance).answer : null,
    numbers: numbers.filter((n) => n && typeof n.text === "string" && Array.isArray(n.sources)),
    more_numbers: Number((value as AnswerProvenance).more_numbers) || 0,
    files: Array.isArray((value as AnswerProvenance).files) ? (value as AnswerProvenance).files.map(String) : [],
  };
}

// --- Marking an answer's numbers, so each can show its sources ---------------

// A minimal HTML syntax tree, as react-markdown's rehype stage gives it.
interface HastText {
  type: "text";
  value: string;
}
interface HastElement {
  type: "element";
  tagName: string;
  properties?: Record<string, unknown>;
  children: HastNode[];
}
type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };

// Where a number isn't a claim, so it's never marked: code and links.
const SKIP = new Set(["code", "pre", "a", "button"]);

// A number and a date, as the backend reads them (sessions/tracing.py _NUMBER,
// _DATE), so the answer marks exactly the tokens that were traced: never "226"
// in "2,226,000", "3" in "\u22123", or "12" in "2024-12-01".
const NUMBER = /(?<![\w.])[-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?!\w)/g;
const DATE = /\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/g;

/**
 * A rehype plugin that wraps each of `numbers` (as written in the answer) in
 * `<span data-number="…">`, outside code and links, so the answer can render
 * it with its sources. Only a whole number token counts, read as the backend
 * reads them, and never one inside a date.
 */
export function markNumbers(numbers: string[]) {
  const wanted = new Set(numbers);

  function split(text: HastText): HastNode[] {
    if (!wanted.size) return [text];
    const dates = Array.from(text.value.matchAll(DATE), (m) => [m.index ?? 0, (m.index ?? 0) + m[0].length]);
    const parts: HastNode[] = [];
    let last = 0;
    for (const match of text.value.matchAll(NUMBER)) {
      const at = match.index ?? 0;
      const end = at + match[0].length;
      if (!wanted.has(match[0]) || dates.some(([from, to]) => at < to && end > from)) continue;
      if (at > last) parts.push({ type: "text", value: text.value.slice(last, at) });
      parts.push({
        type: "element",
        tagName: "span",
        properties: { dataNumber: match[0] },
        children: [{ type: "text", value: match[0] }],
      });
      last = end;
    }
    if (!parts.length) return [text];
    if (last < text.value.length) parts.push({ type: "text", value: text.value.slice(last) });
    return parts;
  }

  function walk(node: HastNode) {
    if (!("children" in node) || !node.children) return;
    if (node.type === "element" && SKIP.has((node as HastElement).tagName)) return;
    node.children = node.children.flatMap((child) => (child.type === "text" ? split(child as HastText) : (walk(child), [child])));
  }

  return () => (tree: HastNode) => {
    walk(tree);
  };
}

// --- Showing a query in the Queries tab ------------------------------------------

/** Asks the workspace's side panel to show a query in its Queries tab. */
export const SHOW_QUERY = "datalab:show-query";

export function showQuery(id: string): void {
  window.dispatchEvent(new CustomEvent(SHOW_QUERY, { detail: { id } }));
}
