// The diff itself, loaded only when a page shows one (see DiffView.tsx).
import {
  diff,
  getChunks,
  getOriginalDoc,
  MergeView,
  originalDocChangeEffect,
  unifiedMergeView,
} from "@codemirror/merge";
import { ChangeSet, Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, gutter, GutterMarker } from "@codemirror/view";
import { useEffect, useId, useRef } from "react";

import { accessibility, baseExtensions, tabHint } from "./CodeMirrorEditor";
import { loadLanguage } from "./languages";
import { changedRange, syncDoc } from "./sync";
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

class Sign extends GutterMarker {
  constructor(readonly sign: string) {
    super();
  }
  eq(other: Sign) {
    return other.sign === this.sign;
  }
  toDOM() {
    return document.createTextNode(this.sign);
  }
}
const added = new Sign("+");
const removed = new Sign("−");

/** + and − beside changed lines, so a change doesn't show by colour alone. */
function signs(): Extension {
  return gutter({
    class: "cm-diffSigns",
    lineMarker(view, line) {
      const { chunks, side } = getChunks(view.state) ?? { chunks: [], side: null };
      const before = side === "a";
      const inside = chunks.some((c) => {
        const [from, to] = before ? [c.fromA, c.toA] : [c.fromB, c.toB];
        return line.from >= from && line.from < to;
      });
      return inside ? (before ? removed : added) : null;
    },
    lineMarkerChange: (update) => getChunks(update.startState)?.chunks !== getChunks(update.state)?.chunks,
  });
}

export default function CodeMirrorDiff({ original, modified, language = "text", label, layout = "split" }: DiffViewProps) {
  const host = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const summary = summaryText(diffSummary(original, modified));
  // Split: `a` is before, `b` after. Unified: `b` alone, with what was removed shown in it.
  const views = useRef<{ a?: EditorView; b: EditorView } | null>(null);
  const slots = useRef({ language: new Compartment(), aria: new Compartment() }).current;
  const latest = useRef({ original, modified, label });
  latest.current = { original, modified, label };

  // Built again only for another layout. Everything else is updated in place, so
  // focus and scroll stay where they were.
  useEffect(() => {
    const { original, modified, label } = latest.current;
    const side = (name: string): Extension[] => [
      baseExtensions(),
      signs(),
      slots.language.of([]),
      EditorState.readOnly.of(true),
      slots.aria.of(accessibility(name, hintId, true, false)),
    ];
    let destroy: () => void;
    if (layout === "split") {
      const merge = new MergeView({
        parent: host.current!,
        a: { doc: original, extensions: side(`${label}, before`) },
        b: { doc: modified, extensions: side(`${label}, after`) },
        collapseUnchanged: { margin: 3, minSize: 6 },
        gutter: true,
      });
      views.current = { a: merge.a, b: merge.b };
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
      views.current = { b: view };
      destroy = () => view.destroy();
    }
    return () => {
      destroy();
      views.current = null;
    };
  }, [layout, hintId, slots]);

  useEffect(() => {
    const { a, b } = views.current!;
    if (a) return syncDoc(a, original);
    const before = getOriginalDoc(b.state);
    const change = changedRange(before.toString(), original);
    if (change) b.dispatch({ effects: originalDocChangeEffect(b.state, ChangeSet.of(change, before.length)) });
  }, [original, layout]);

  useEffect(() => syncDoc(views.current!.b, modified), [modified, layout]);

  useEffect(() => {
    const { a, b } = views.current!;
    a?.dispatch({ effects: slots.aria.reconfigure(accessibility(`${label}, before`, hintId, true, false)) });
    b.dispatch({
      effects: slots.aria.reconfigure(accessibility(`${label}, ${a ? "after" : "changes"}`, hintId, true, false)),
    });
  }, [label, layout, hintId, slots]);

  useEffect(() => {
    let cancelled = false;
    loadLanguage(language).then((extension) => {
      const { a, b } = views.current ?? {};
      if (cancelled) return;
      for (const view of [a, b]) view?.dispatch({ effects: slots.language.reconfigure(extension) });
    });
    return () => {
      cancelled = true;
    };
  }, [language, layout, slots]);

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
