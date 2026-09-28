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
  const [side, setSide] = useState<{ x: "start" | "end"; y: "below" | "above"; width: number }>({
    x: align ?? "start",
    y: "below",
    width: WIDTH,
  });
  const open = pinned || hover;

  // Where there's room for it, measured as it opens: within the window, and
  // within whatever scrolls around it (a narrow side panel), so it's never
  // cut off or makes the panel scroll sideways.
  useEffect(() => {
    if (!open || !trigger.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const clip = clippingBox(trigger.current);
    const toRight = clip.right - rect.left - GAP; // room for a tip lined up with the start
    const toLeft = rect.right - clip.left - GAP; // and with the end
    const roomFor = (side: "start" | "end") => (side === "start" ? toRight : toLeft);
    let x: "start" | "end" = align ?? (toRight >= WIDTH ? "start" : "end");
    // The side asked for, unless it's too tight and the other has more room.
    const other = x === "start" ? "end" : "start";
    if (roomFor(x) < WIDTH && roomFor(other) > roomFor(x)) x = other;
    const room = roomFor(x);
    setSide({
      x,
      y: rect.bottom + 220 > clip.bottom && rect.top - clip.top > 220 ? "above" : "below",
      width: Math.max(MIN_WIDTH, Math.min(WIDTH, room)),
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

  // Escape hides a tip wherever focus is, even one only hovered over (WCAG
  // 1.4.13): without moving the mouse away, the person can get it out of the
  // way. With focus inside, the wrapper's own handler does it, and gives focus
  // back to the button.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || box.current?.contains(document.activeElement)) return;
      // Only the tip: a dialog behind it stays open.
      event.stopPropagation();
      setPinned(false);
      setHover(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

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
            ? "inline-flex items-center rounded-[2px] text-left hover:underline hover:decoration-dotted hover:underline-offset-4"
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
          "absolute z-40",
          side.x === "start" ? "left-0" : "right-0",
          side.y === "below" ? "top-full pt-1.5" : "bottom-full pb-1.5",
          open ? "visible" : "invisible",
        )}
        style={{ width: side.width }}
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

const WIDTH = 304;
const MIN_WIDTH = 180;
const GAP = 8;

/** The part of the window an element can be seen in: the window, cut by every ancestor that scrolls or clips. */
function clippingBox(element: HTMLElement): { left: number; right: number; top: number; bottom: number } {
  const box = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
  for (let el = element.parentElement; el && el !== document.body; el = el.parentElement) {
    const style = getComputedStyle(el);
    if (/(auto|scroll|hidden|clip)/.test(style.overflowX + style.overflowY)) {
      const r = el.getBoundingClientRect();
      box.left = Math.max(box.left, r.left);
      box.right = Math.min(box.right, r.right);
      box.top = Math.max(box.top, r.top);
      box.bottom = Math.min(box.bottom, r.bottom);
    }
  }
  return box;
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
