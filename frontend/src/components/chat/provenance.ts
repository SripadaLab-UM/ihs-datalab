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

/**
 * A rehype plugin that wraps each of `numbers` (as written in the answer) in
 * `<span data-number="…">`, outside code and links, so the answer can render
 * it with its sources. A number is matched whole: "81" isn't in "181" or "8.1".
 */
export function markNumbers(numbers: string[]) {
  const wanted = [...new Set(numbers)].sort((a, b) => b.length - a.length);
  const escaped = wanted.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = wanted.length ? new RegExp(`(?<![\\w.])(${escaped.join("|")})(?![\\w]|\\.\\d)`, "g") : null;

  function split(text: HastText): HastNode[] {
    if (!pattern) return [text];
    const parts: HastNode[] = [];
    let last = 0;
    for (const match of text.value.matchAll(pattern)) {
      const at = match.index ?? 0;
      if (at > last) parts.push({ type: "text", value: text.value.slice(last, at) });
      parts.push({
        type: "element",
        tagName: "span",
        properties: { dataNumber: match[1] },
        children: [{ type: "text", value: match[1] }],
      });
      last = at + match[0].length;
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

// --- Showing a query in the Data accessed log --------------------------------

/** Asks the workspace's side panel to show a query in its Data accessed log. */
export const SHOW_QUERY = "datalab:show-query";

export function showQuery(id: string): void {
  window.dispatchEvent(new CustomEvent(SHOW_QUERY, { detail: { id } }));
}
