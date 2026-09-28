// The three-panel layout of the SQL Playground, Workflows, Pipelines and
// Knowledge tabs: a list on the left, the page, and a docked chat on the right.
// The person can drag the dividers between them (or move them from the
// keyboard), and each tab remembers its widths on this computer. On a narrow
// window the list and the chat are drawers over the page instead, and the
// dividers go.
import clsx from "clsx";
import { type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode, type RefObject, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/components/ui/overlay";

/** At least this wide, the list sits beside the page; narrower, it's a drawer. */
export const NAV_DOCK = "(min-width: 1024px)";
/** At least this wide, the chat sits beside the page; narrower, it's a drawer. */
export const CHAT_DOCK = "(min-width: 1100px)";

export const LIMITS = {
  nav: { min: 200, max: 440 },
  chat: { min: 320, max: 760 },
  /** The page between them never gets narrower than this while both are docked. */
  content: 420,
} as const;

/** Arrow keys move a divider this far; with Shift, `BIG_STEP`. */
export const STEP = 16;
export const BIG_STEP = 64;

export type Side = "nav" | "chat";
interface Widths {
  nav?: number;
  chat?: number;
}

const storageKey = (tab: string) => `datalab:panels:${tab}`;

function readWidths(tab: string): Widths {
  try {
    const raw = localStorage.getItem(storageKey(tab));
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (!value || typeof value !== "object") return {};
    const { nav, chat } = value as Record<string, unknown>;
    return {
      nav: typeof nav === "number" && Number.isFinite(nav) ? nav : undefined,
      chat: typeof chat === "number" && Number.isFinite(chat) ? chat : undefined,
    };
  } catch {
    return {};
  }
}

function writeWidths(tab: string, widths: Widths) {
  try {
    if (widths.nav === undefined && widths.chat === undefined) localStorage.removeItem(storageKey(tab));
    else localStorage.setItem(storageKey(tab), JSON.stringify(widths));
  } catch {
    // Storage can be refused (a private window): the widths last until the page goes.
  }
}

/** The widths the tab starts with, for a window this wide: what the fixed layout used to be. */
export function defaultWidths(total: number, navDefault = 272): { nav: number; chat: number } {
  const roomy = total >= 1536;
  return { nav: roomy ? navDefault + 16 : navDefault, chat: roomy ? 480 : 416 };
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max));

/**
 * The widths as shown in a container `total` wide: each within its limits,
 * and, when both are docked, the chat and then the list give way so the page
 * keeps `LIMITS.content`. Returns each panel's allowed range too.
 */
export function fit(total: number, wanted: { nav: number; chat: number }, docked: { nav: boolean; chat: boolean }) {
  let nav = docked.nav ? clamp(wanted.nav, LIMITS.nav.min, LIMITS.nav.max) : 0;
  let chat = docked.chat ? clamp(wanted.chat, LIMITS.chat.min, LIMITS.chat.max) : 0;
  let over = nav + chat + LIMITS.content - total;
  if (over > 0 && docked.chat) {
    const give = Math.min(over, chat - LIMITS.chat.min);
    chat -= give;
    over -= give;
  }
  if (over > 0 && docked.nav) nav -= Math.min(over, nav - LIMITS.nav.min);
  const room = (other: number) => total - other - LIMITS.content;
  return {
    nav,
    chat,
    navMax: clamp(Math.min(LIMITS.nav.max, room(chat)), LIMITS.nav.min, LIMITS.nav.max),
    chatMax: clamp(Math.min(LIMITS.chat.max, room(nav)), LIMITS.chat.min, LIMITS.chat.max),
  };
}

/** How wide an element is, following changes (the window's width where it can't be measured). */
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(() => (typeof window !== "undefined" ? window.innerWidth : 1280));
  useLayoutEffect(() => {
    const box = ref.current;
    const measure = () => {
      const measured = box?.getBoundingClientRect().width ?? 0;
      setWidth(measured > 0 ? measured : window.innerWidth);
    };
    measure();
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver !== "undefined" && box ? new ResizeObserver(measure) : null;
    if (box) observer?.observe(box);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [ref]);
  return width;
}

export interface Panels {
  /** On the layout's container: its grid template. */
  ref: RefObject<HTMLDivElement | null>;
  style: CSSProperties;
  /** Whether each sits beside the page (else it's a drawer). */
  navDocked: boolean;
  chatDocked: boolean;
  /** The dividers, to render inside the container (they place themselves). */
  dividers: ReactNode;
  /** The ids to give the list and the chat, which the dividers name as what they resize. */
  navId: string;
  chatId: string;
}

/**
 * The layout of one tab (`tab` names its remembered widths). `nav`: whether it
 * has a list on the left; `chatOpen`: whether its chat is shown.
 */
export function usePanels(tab: string, { nav = true, chatOpen, navDefault }: { nav?: boolean; chatOpen: boolean; navDefault?: number }): Panels {
  const ref = useRef<HTMLDivElement>(null);
  const total = useWidth(ref);
  const navDocked = useMediaQuery(NAV_DOCK) && nav;
  const chatDocked = useMediaQuery(CHAT_DOCK);
  const [stored, setStored] = useState<Widths>(() => readWidths(tab));
  // Another tab's widths when the component is reused for one (never in practice, but cheap).
  const [storedTab, setStoredTab] = useState(tab);
  if (storedTab !== tab) {
    setStoredTab(tab);
    setStored(readWidths(tab));
  }
  const defaults = defaultWidths(total, navDefault);
  const docked = { nav: navDocked, chat: chatDocked && chatOpen };
  const shown = fit(total, { nav: stored.nav ?? defaults.nav, chat: stored.chat ?? defaults.chat }, docked);

  // Saved after each change the person makes (not when read, or for another tab's widths).
  const changed = useRef(false);
  useEffect(() => {
    if (!changed.current) return;
    changed.current = false;
    writeWidths(tab, stored);
  }, [tab, stored]);
  const set = useCallback((side: Side, width: number | undefined) => {
    changed.current = true;
    setStored((before) => ({ ...before, [side]: width === undefined ? undefined : Math.round(width) }));
  }, []);
  const reset = useCallback(() => {
    changed.current = true;
    setStored({});
  }, []);
  const ids = useId();
  const navId = `${ids}-nav`;
  const chatId = `${ids}-chat`;

  const columns = [docked.nav && `${shown.nav}px`, "minmax(0,1fr)", docked.chat && `${shown.chat}px`].filter(Boolean).join(" ");
  const dividers = (
    <>
      {docked.nav && (
        <Divider
          side="nav"
          label="Resize the list"
          value={shown.nav}
          min={LIMITS.nav.min}
          max={shown.navMax}
          at={{ left: shown.nav }}
          controls={navId}
          onChange={(w) => set("nav", w)}
          onReset={() => set("nav", undefined)}
          onResetAll={reset}
        />
      )}
      {docked.chat && (
        <Divider
          side="chat"
          label="Resize the chat"
          value={shown.chat}
          min={LIMITS.chat.min}
          max={shown.chatMax}
          at={{ right: shown.chat }}
          controls={chatId}
          onChange={(w) => set("chat", w)}
          onReset={() => set("chat", undefined)}
          onResetAll={reset}
        />
      )}
    </>
  );
  return { ref, style: { gridTemplateColumns: columns }, navDocked, chatDocked, dividers, navId, chatId };
}

/**
 * The line between two panels, as a WAI-ARIA window splitter: drag it, or
 * focus it and use the arrow keys (Shift for bigger steps), Home and End for
 * the narrowest and widest, Enter or a double-click for the default width.
 * Its value is the width of the panel it belongs to.
 */
export function Divider({
  side,
  label,
  value,
  min,
  max,
  at,
  controls,
  onChange,
  onReset,
  onResetAll,
}: {
  side: Side;
  label: string;
  value: number;
  min: number;
  max: number;
  at: { left?: number; right?: number };
  /** The id of the panel it resizes. */
  controls: string;
  onChange: (width: number) => void;
  onReset: () => void;
  onResetAll: () => void;
}) {
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  // The chat's divider grows the chat as it moves left.
  const direction = side === "nav" ? 1 : -1;
  const move = (width: number) => onChange(clamp(width, min, max));

  useEffect(() => {
    if (!dragging) return;
    // No text selected, and the resize cursor everywhere, while dragging.
    const { userSelect, cursor } = document.body.style;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      document.body.style.userSelect = userSelect;
      document.body.style.cursor = cursor;
    };
  }, [dragging]);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.currentTarget.focus();
    drag.current = { x: event.clientX, width: value };
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    move(drag.current.width + direction * (event.clientX - drag.current.x));
  };
  const stop = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? BIG_STEP : STEP;
    const keys: Record<string, () => void> = {
      ArrowRight: () => move(value + direction * step),
      ArrowLeft: () => move(value - direction * step),
      Home: () => move(min),
      End: () => move(max),
      Enter: onReset,
    };
    const act = keys[event.key];
    if (!act) return;
    event.preventDefault();
    act();
  };

  return (
    <div
      data-divider={side}
      className="group absolute inset-y-0 z-10 w-3 -translate-x-1/2"
      style={at.left !== undefined ? { left: at.left } : { left: `calc(100% - ${at.right}px)` }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={label}
        aria-controls={controls}
        aria-valuenow={Math.round(value)}
        aria-valuemin={min}
        aria-valuemax={Math.round(max)}
        aria-valuetext={`${Math.round(value)} pixels wide`}
        title="Drag, or use the arrow keys, to resize. Double-click or press Enter for the default width."
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={stop}
        onPointerCancel={stop}
        onLostPointerCapture={stop}
        onDoubleClick={onReset}
        onKeyDown={onKeyDown}
        className="peer flex h-full w-full cursor-col-resize touch-none justify-center outline-none"
      >
        {/* The grip: the panel's hairline, drawn in ink while hovered, focused or dragged. */}
        <span
          aria-hidden
          className={clsx(
            "h-full w-[3px] rounded-full transition-colors",
            dragging ? "bg-ink" : "bg-transparent group-hover:bg-edge group-has-[:focus-visible]:bg-ink",
          )}
        />
      </div>
      <button
        type="button"
        onClick={onResetAll}
        className={clsx(
          "absolute top-2 left-1/2 -translate-x-1/2 rounded-[3px] border border-line bg-surface px-1.5 py-0.5 font-sans text-[11.5px] whitespace-nowrap text-muted shadow-sm",
          // Shown while the divider is hovered or has keyboard focus, and while it has focus itself (Tab from the
          // divider). Unseen, it takes no clicks meant for what's under it.
          "pointer-events-none opacity-0 enabled:hover:text-ink",
          "peer-hover:pointer-events-auto peer-hover:opacity-100 peer-focus-visible:opacity-100",
          "hover:pointer-events-auto hover:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100",
          dragging && "hidden",
        )}
      >
        Reset panel widths
      </button>
    </div>
  );
}

/** The list on the left: beside the page, a drawer over it while `drawer` is open, or out of sight. */
export function navClass(docked: boolean, drawer: boolean): string {
  return clsx(
    "min-h-0 overflow-hidden border-r border-line bg-rail",
    docked ? "block" : drawer ? "absolute inset-y-0 left-0 z-30 w-[min(18rem,100%)] shadow-xl" : "hidden",
  );
}

/** The chat on the right: beside the page, or a drawer over it. */
export function chatClass(docked: boolean): string {
  return clsx(
    "flex min-h-0 min-w-0 flex-col border-l border-line bg-surface",
    !docked && "absolute inset-y-0 right-0 z-30 w-[min(28rem,100%)] shadow-xl",
  );
}

/**
 * Whether to render a panel that can be closed: not until it's first opened,
 * then for good, so closing and reopening it (a chat, say) keeps its draft,
 * scroll position and stream instead of starting again.
 */
export function useKeptOnceOpen(open: boolean): boolean {
  const [kept, setKept] = useState(open);
  if (open && !kept) setKept(true);
  return kept || open;
}

/** A chat drawer's first focus: its message box. */
export const messageBox = (box: HTMLElement): HTMLElement | null => box.querySelector<HTMLElement>("textarea:not([disabled])");
