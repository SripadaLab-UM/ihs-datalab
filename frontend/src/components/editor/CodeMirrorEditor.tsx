// The editor itself, loaded only when a page shows one (see CodeEditor.tsx).
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, indentOnInput } from "@codemirror/language";
import { lintKeymap, setDiagnostics } from "@codemirror/lint";
import { Annotation, Compartment, EditorState, type Extension, Prec } from "@codemirror/state";
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { useEffect, useId, useRef } from "react";

import { toRanges } from "./diagnostics";
import { loadLanguage } from "./languages";
import { syncDoc } from "./sync";
import { paper } from "./theme";
import type { CodeEditorProps, EditorLanguage } from "./types";

// Marks a change that came from `value`, so it isn't reported back through onChange.
const fromProps = Annotation.define<boolean>();

/** Everything that doesn't change with the props. */
export function baseExtensions(): Extension[] {
  return [
    lineNumbers(),
    highlightActiveLineGutter(),
    history(),
    drawSelection(),
    indentOnInput(),
    bracketMatching(),
    highlightActiveLine(),
    // Tab indents; Escape then Tab moves on, so the keyboard is never trapped.
    keymap.of([...defaultKeymap, ...historyKeymap, ...lintKeymap, indentWithTab]),
    escapeThenTab(),
    paper,
  ];
}

/** What Tab does, for screen readers. Read-only text doesn't take Tab, so it moves on as usual. */
export const tabHint = (readOnly: boolean) =>
  readOnly ? "Read-only." : "Tab indents. To leave the editor, press Escape, then Tab.";

const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta"]);

/**
 * Escape lets the next Tab leave the editor, with no time limit (CodeMirror's
 * own lasts two seconds): any other key, or leaving, ends it. The editor keeps
 * its Escape, so it doesn't also close a dialog or drawer the editor is in.
 */
function escapeThenTab(): Extension {
  let escaped = false;
  const end = (view: EditorView) => {
    if (escaped) view.setTabFocusMode(false);
    escaped = false;
  };
  // Observers see every key, even one a key binding has already handled.
  return EditorView.domEventObservers({
    keydown(event, view) {
      if (event.key === "Escape") {
        event.stopPropagation();
        escaped = true;
        view.setTabFocusMode(true);
      } else if (escaped && event.key !== "Tab" && !MODIFIERS.has(event.key)) {
        end(view);
      }
    },
    blur(_event, view) {
      end(view);
    },
  });
}

export function accessibility(label: string, hintId: string, readOnly: boolean, invalid: boolean): Extension {
  return EditorView.contentAttributes.of({
    "aria-label": label,
    "aria-describedby": hintId,
    "aria-readonly": String(readOnly),
    ...(invalid ? { "aria-invalid": "true" } : {}),
  });
}

/** Ctrl+Enter (⌘+Enter on a Mac), ahead of the default keymap's own use of it. */
function submitKey(run: (() => void) | null): Extension {
  return run ? Prec.highest(keymap.of([{ key: "Mod-Enter", preventDefault: true, run: () => (run(), true) }])) : [];
}

export default function CodeMirrorEditor({
  value,
  onChange,
  language = "text",
  readOnly = false,
  diagnostics,
  label,
  onSubmit,
  extensions,
}: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const compartments = useRef({
    language: new Compartment(),
    readOnly: new Compartment(),
    aria: new Compartment(),
    submit: new Compartment(),
    extra: new Compartment(),
  }).current;
  // The latest callbacks, without rebuilding the editor when they change.
  const callbacks = useRef({ onChange, onSubmit });
  callbacks.current = { onChange, onSubmit };
  const invalid = diagnostics?.some((d) => d.severity === "error") ?? false;
  const hintId = useId();
  const submit = onSubmit ? () => callbacks.current.onSubmit?.() : null;
  // The latest `value`, and whether one arrived while an input method was composing.
  const latest = useRef(value);
  latest.current = value;
  const waiting = useRef(false);
  const sync = useRef(() => {
    const editor = view.current;
    if (!editor) return;
    // Changing the text mid-composition breaks the input method: wait for it to finish.
    if (editor.compositionStarted) {
      waiting.current = true;
      return;
    }
    waiting.current = false;
    syncDoc(editor, latest.current, fromProps.of(true));
  }).current;

  useEffect(() => {
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          baseExtensions(),
          compartments.language.of([]),
          compartments.readOnly.of(EditorState.readOnly.of(readOnly)),
          compartments.aria.of(accessibility(label, hintId, readOnly, invalid)),
          compartments.submit.of(submitKey(submit)),
          compartments.extra.of(extensions ?? []),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !update.transactions.some((tr) => tr.annotation(fromProps))) {
              callbacks.current.onChange?.(update.state.doc.toString());
            }
          }),
        ],
      }),
    });
    view.current = editor;
    // After the composition's own change is in, and only if a value came meanwhile.
    const afterComposing = () => waiting.current && setTimeout(sync);
    editor.contentDOM.addEventListener("compositionend", afterComposing);
    return () => {
      editor.contentDOM.removeEventListener("compositionend", afterComposing);
      editor.destroy();
      view.current = null;
    };
    // Built once; the effects below keep it up to date with the props.
  }, []);

  // Only what differs is changed, so the cursor stays where it was.
  useEffect(sync, [value, sync]);

  useEffect(() => {
    let cancelled = false;
    loadLanguage(language as EditorLanguage).then((extension) => {
      if (!cancelled) view.current?.dispatch({ effects: compartments.language.reconfigure(extension) });
    });
    return () => {
      cancelled = true;
    };
  }, [language, compartments]);

  useEffect(() => {
    view.current!.dispatch({
      effects: [
        compartments.readOnly.reconfigure(EditorState.readOnly.of(readOnly)),
        compartments.aria.reconfigure(accessibility(label, hintId, readOnly, invalid)),
      ],
    });
  }, [readOnly, label, hintId, invalid, compartments]);

  // Only whether there is one: the latest is always called.
  const submits = Boolean(onSubmit);
  useEffect(() => {
    view.current!.dispatch({
      effects: compartments.submit.reconfigure(submitKey(submits ? () => callbacks.current.onSubmit?.() : null)),
    });
  }, [submits, compartments]);

  useEffect(() => {
    view.current!.dispatch({ effects: compartments.extra.reconfigure(extensions ?? []) });
  }, [extensions, compartments]);

  // Placed when they change (after `value`, if both did), then moved along with
  // the text as it's edited, until the next report.
  useEffect(() => {
    const editor = view.current!;
    editor.dispatch(setDiagnostics(editor.state, toRanges(editor.state.doc, diagnostics ?? [])));
  }, [diagnostics]);

  return (
    <>
      <div ref={host} className="h-full" />
      <span id={hintId} className="sr-only">
        {tabHint(readOnly)}
      </span>
    </>
  );
}
