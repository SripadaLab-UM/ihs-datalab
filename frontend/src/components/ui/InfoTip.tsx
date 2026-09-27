import clsx from "clsx";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link, useInRouterContext, useLocation } from "react-router";

import { glossaryTerm, helpPath } from "@/lib/guide";

/**
 * Every glossary term a tooltip may use. The words themselves are in
 * docs/guide/glossary.md: each term's first paragraph is its tooltip.
 */
export const TOOLTIP_TERMS = [
  "data-session",
  "research-session",
  "rigor-review",
  "plan",
  "trace-and-provenance",
  "checkpoint",
  "export",
  "data-accessed",
  "read-only",
  "practice",
  "proposal",
  "save--share",
  "workflow",
  "pipeline",
  "replay",
  "small-cells",
] as const;

export type TermId = (typeof TOOLTIP_TERMS)[number];

/**
 * What one of DataLab's own words means, from the glossary, with "Learn more"
 * into Help. It's a toggletip: hovering shows it, and so do a click, Enter
 * or Space, for the keyboard; Escape or clicking elsewhere hides it. The
 * definition is always in the page, as the trigger's description, so a
 * screen reader reads it when the trigger is focused.
 *
 * With `children`, they are the trigger (the session badge, say); otherwise
 * it's a small "i".
 */
export function InfoTip({
  term,
  children,
  className,
  align,
}: {
  term: TermId;
  children?: ReactNode;
  className?: string;
  /** Which edge of the trigger the text lines up with; by default whichever has room. */
  align?: "start" | "end";
}) {
  const entry = glossaryTerm(term);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [pinned, setPinned] = useState(false);
  const [hover, setHover] = useState(false);
  const [side, setSide] = useState<{ x: "start" | "end"; y: "below" | "above" }>({ x: align ?? "start", y: "below" });
  const open = pinned || hover;

  // Where there's room for it, measured as it opens.
  useEffect(() => {
    if (!open || !trigger.current) return;
    const rect = trigger.current.getBoundingClientRect();
    setSide({
      x: align ?? (rect.left + 320 > window.innerWidth ? "end" : "start"),
      y: rect.bottom + 220 > window.innerHeight && rect.top > 220 ? "above" : "below",
    });
  }, [open, align]);

  // A click anywhere else hides a tip opened by clicking.
  useEffect(() => {
    if (!pinned) return;
    const away = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setPinned(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [pinned]);

  if (!entry) return <>{children}</>;
  const tipId = `${id}-tip`;
  return (
    <span
      ref={box}
      className={clsx("relative inline-flex items-center", className)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          setPinned(false);
          setHover(false);
          trigger.current?.focus();
        }
      }}
      onBlur={(event) => {
        if (!box.current?.contains(event.relatedTarget as Node)) setPinned(false);
      }}
    >
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-controls={tipId}
        aria-describedby={`${id}-text`}
        aria-label={children ? undefined : `About “${entry.term}”`}
        onClick={() => {
          if (pinned) {
            setPinned(false);
            setHover(false);
          } else setPinned(true);
        }}
        className={clsx(
          children
            ? "inline-flex items-center rounded-[2px] text-left"
            : "inline-flex size-[18px] items-center justify-center rounded-full text-faint hover:text-ink",
        )}
      >
        {children ?? (
          <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={1.9} aria-hidden="true">
            <circle cx="12" cy="12" r="8.5" />
            <path d="M12 11v5.5M12 7.8v.2" strokeLinecap="round" />
          </svg>
        )}
      </button>
      {/* Always in the page, for the description; shown only while open. */}
      <span
        id={tipId}
        className={clsx(
          "absolute z-40 w-[min(19rem,80vw)]",
          side.x === "start" ? "left-0" : "right-0",
          side.y === "below" ? "top-full pt-1.5" : "bottom-full pb-1.5",
          open ? "visible" : "invisible",
        )}
      >
        <span className="dl-in block rounded-[3px] border border-line bg-surface px-3.5 py-3 text-left font-sans text-[13px] leading-relaxed font-normal tracking-normal text-ink normal-case shadow-[0_12px_32px_-16px_rgba(0,0,0,0.35)]">
          {/* A trigger with its own words (the session badge) already names it. */}
          {!children && <span className="dl-label mb-1 block">{entry.term}</span>}
          <span id={`${id}-text`} className="block">
            {entry.short}
          </span>
          <LearnMore to={helpPath({ slug: "glossary" }, entry.id)} term={entry.term} />
        </span>
      </span>
    </span>
  );
}

const LEARN_MORE = "mt-2 inline-block text-ink underline decoration-faint underline-offset-4 hover:decoration-ink";

/** Into Help, remembering where from, so Help can offer the way back. */
function LearnMore({ to, term }: { to: string; term: string }) {
  // The visible words first, so a spoken command ("click Learn more") still finds it.
  const label = `Learn more about “${term}” in Help`;
  // Outside the app's router (a component shown on its own), a plain link.
  return useInRouterContext() ? (
    <RoutedLearnMore to={to} label={label} />
  ) : (
    <a href={to} aria-label={label} className={LEARN_MORE}>
      Learn more
    </a>
  );
}

function RoutedLearnMore({ to, label }: { to: string; label: string }) {
  const location = useLocation();
  return (
    <Link
      to={to}
      aria-label={label}
      state={{ from: `${location.pathname}${location.search}${location.hash}` }}
      className={LEARN_MORE}
    >
      Learn more
    </Link>
  );
}
