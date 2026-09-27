import type { EditorView } from "@codemirror/view";

/** The one change that turns `current` into `next`: only the part between what they share at
 *  the start and the end, so the cursor, selection and scroll elsewhere stay put. */
export function changedRange(current: string, next: string): { from: number; to: number; insert: string } | null {
  if (current === next) return null;
  const shortest = Math.min(current.length, next.length);
  let start = 0;
  while (start < shortest && current.charCodeAt(start) === next.charCodeAt(start)) start++;
  let end = 0;
  while (end < shortest - start && current.charCodeAt(current.length - 1 - end) === next.charCodeAt(next.length - 1 - end)) end++;
  return { from: start, to: current.length - end, insert: next.slice(start, next.length - end) };
}

/** A view's text as `next`, changing only what differs. */
export function syncDoc(view: EditorView, next: string, annotations?: Parameters<EditorView["dispatch"]>[0]["annotations"]) {
  const change = changedRange(view.state.doc.toString(), next);
  if (change) view.dispatch({ changes: change, annotations });
}
