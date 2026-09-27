import clsx from "clsx";
import { lazy, Suspense } from "react";

import { Loading } from "./CodeEditor";
import type { DiffViewProps } from "./types";

export type { DiffViewProps } from "./types";

const CodeMirrorDiff = lazy(() => import("./CodeMirrorDiff"));

/** Two versions of a text, compared: read-only, with what changed marked. */
export function DiffView({ className, ...props }: DiffViewProps) {
  return (
    <div className={clsx("overflow-hidden rounded-[4px] border border-line bg-field", className)}>
      <Suspense fallback={<Loading value={props.modified} label={props.label} />}>
        <CodeMirrorDiff {...props} />
      </Suspense>
    </div>
  );
}
