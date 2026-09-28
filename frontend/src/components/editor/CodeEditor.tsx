import clsx from "clsx";
import { lazy, Suspense } from "react";

import type { CodeEditorProps } from "./types";

export type { CodeEditorProps, EditorDiagnostic, EditorLanguage } from "./types";

// CodeMirror is its own chunk, fetched from DataLab the first time a page shows an editor.
const CodeMirrorEditor = lazy(() => import("./CodeMirrorEditor"));

/** The shared code editor: controlled, like a textarea. SQL, YAML, Markdown, R, Python, shell, JSON or plain text. */
export function CodeEditor({ className, ...props }: CodeEditorProps) {
  return (
    <div
      className={clsx(
        "min-h-24 overflow-hidden rounded-[4px] border border-line bg-field transition-colors focus-within:border-ink focus-within:shadow-[0_0_0_1px_var(--color-ink)]",
        className,
      )}
    >
      <Suspense fallback={<Loading value={props.value} label={props.label} />}>
        <CodeMirrorEditor {...props} />
      </Suspense>
    </div>
  );
}

/** The text while the editor loads, where it will be, so nothing moves when it arrives. */
export function Loading({ value, label }: { value: string; label: string }) {
  return (
    <pre
      aria-busy="true"
      aria-label={`${label} (loading the editor)`}
      className="h-full overflow-auto py-2 pl-[3.25rem] font-mono text-[12.5px] leading-[1.6] whitespace-pre text-ink"
    >
      {value}
    </pre>
  );
}
