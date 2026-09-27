// The diff itself, loaded only when a page shows one (see DiffView.tsx).
import { diff, MergeView, unifiedMergeView } from "@codemirror/merge";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useEffect, useId, useRef } from "react";

import { accessibility, baseExtensions, tabHint } from "./CodeMirrorEditor";
import { loadLanguage } from "./languages";
import type { DiffViewProps } from "./types";

/** How many lines were added and removed: a line-by-line diff, each distinct line
 *  standing in as one character so the character diff compares whole lines. */
export function diffSummary(original: string, modified: string): { added: number; removed: number } {
  const codes = new Map<string, string>();
  const encode = (text: string) =>
    text
      .split("\n")
      .map((line) => {
        let code = codes.get(line);
        if (code === undefined) {
          const n = 0x100 + codes.size;
          // Past the surrogates, so each line stays one UTF-16 unit (63,000 distinct lines).
          code = String.fromCharCode(n < 0xd800 ? n : n + 0x800);
          codes.set(line, code);
        }
        return code;
      })
      .join("");
  let added = 0;
  let removed = 0;
  for (const change of diff(encode(original), encode(modified))) {
    removed += change.toA - change.fromA;
    added += change.toB - change.fromB;
  }
  return { added, removed };
}

function summaryText({ added, removed }: { added: number; removed: number }): string {
  if (!added && !removed) return "No changes";
  const lines = (n: number) => `${n} line${n === 1 ? "" : "s"}`;
  return [added && `${lines(added)} added`, removed && `${lines(removed)} removed`].filter(Boolean).join(" · ");
}

export default function CodeMirrorDiff({ original, modified, language = "text", label, layout = "split" }: DiffViewProps) {
  const host = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const summary = summaryText(diffSummary(original, modified));

  useEffect(() => {
    const languageSlot = new Compartment();
    const side = (name: string): Extension[] => [
      baseExtensions(),
      languageSlot.of([]),
      EditorState.readOnly.of(true),
      accessibility(name, hintId, true, false),
    ];
    let views: EditorView[];
    let destroy: () => void;
    if (layout === "split") {
      const merge = new MergeView({
        parent: host.current!,
        a: { doc: original, extensions: side(`${label}, before`) },
        b: { doc: modified, extensions: side(`${label}, after`) },
        collapseUnchanged: { margin: 3, minSize: 6 },
        gutter: true,
      });
      views = [merge.a, merge.b];
      destroy = () => merge.destroy();
    } else {
      const view = new EditorView({
        parent: host.current!,
        state: EditorState.create({
          doc: modified,
          extensions: [
            side(`${label}, changes`),
            unifiedMergeView({ original, mergeControls: false, collapseUnchanged: { margin: 3, minSize: 6 } }),
          ],
        }),
      });
      views = [view];
      destroy = () => view.destroy();
    }
    let cancelled = false;
    loadLanguage(language).then((extension) => {
      if (!cancelled) for (const view of views) view.dispatch({ effects: languageSlot.reconfigure(extension) });
    });
    return () => {
      cancelled = true;
      destroy();
    };
  }, [original, modified, language, label, layout, hintId]);

  return (
    <>
      <p className="dl-label border-b border-line px-3 py-1.5" aria-live="polite">
        {summary}
      </p>
      <div ref={host} className="h-full [&_.cm-mergeView]:h-full" />
      <span id={hintId} className="sr-only">
        {tabHint(true)}
      </span>
    </>
  );
}
