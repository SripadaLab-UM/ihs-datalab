// Lets the page put text at the cursor in the editor (a table or column name from
// the catalog). Loaded on its own, so CodeMirror stays out of the page's chunk.
import type { Extension } from "@codemirror/state";
import { type EditorView, ViewPlugin } from "@codemirror/view";

/** An extension that hands the editor's view to `inserter`, while the editor exists. */
export function bridge(inserter: { view: EditorView | null }): Extension {
  return ViewPlugin.define((view) => {
    inserter.view = view;
    return {
      destroy() {
        if (inserter.view === view) inserter.view = null;
      },
    };
  });
}

/** Insert `text` at the cursor (over any selection), as if typed, and give the editor focus. */
export function insertAt(view: EditorView, text: string): void {
  const { from, to } = view.state.selection.main;
  const before = view.state.sliceDoc(Math.max(0, from - 1), from);
  // A space between it and a word just before, so names don't run together.
  const spaced = /[\w$#"]/.test(before) ? ` ${text}` : text;
  view.dispatch({
    changes: { from, to, insert: spaced },
    selection: { anchor: from + spaced.length },
    scrollIntoView: true,
    userEvent: "input",
  });
  view.focus();
}
