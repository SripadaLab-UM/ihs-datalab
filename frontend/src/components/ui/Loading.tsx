import clsx from "clsx";

import { Button } from "./index";

/**
 * A list still loading: a few quiet placeholder rows the shape of the rows to
 * come, and what's loading, said once to a screen reader. Shown instead of an
 * empty state, which only appears once the list has loaded and is empty.
 */
export function LoadingRows({
  label,
  rows = 4,
  dense = false,
  quiet = false,
  className,
}: {
  label: string;
  rows?: number;
  dense?: boolean;
  /** Not announced: another status on the page already says what's loading. */
  quiet?: boolean;
  className?: string;
}) {
  const status = quiet ? {} : ({ role: "status", "aria-live": "polite" } as const);
  return (
    <div {...status} data-loading className={clsx("flex flex-col", className)}>
      <p className="font-sans text-[12.5px] text-muted">{label}</p>
      <ul aria-hidden className={clsx("flex flex-col", dense ? "mt-2 gap-2" : "mt-3 border-t border-line")}>
        {Array.from({ length: rows }, (_, i) => (
          <li key={i} className={clsx("flex flex-col gap-1.5", !dense && "border-b border-line py-3")}>
            <span className="h-3 rounded-[2px] bg-sunken motion-safe:animate-pulse" style={{ width: `${[46, 62, 38, 54, 42][i % 5]}%` }} />
            {!dense && <span className="h-2.5 w-3/4 rounded-[2px] bg-sunken motion-safe:animate-pulse" />}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A list that couldn't be loaded: why, and Retry. */
export function LoadFailed({ message, onRetry, retrying = false }: { message: string; onRetry: () => void; retrying?: boolean }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 font-sans text-[13px]">
      <span className="text-danger">{message}</span>
      <Button variant="secondary" className="px-2.5 py-1 text-[12.5px]" onClick={onRetry} disabled={retrying}>
        {retrying ? "Trying again…" : "Retry"}
      </Button>
    </div>
  );
}
