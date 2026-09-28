// The toolbar's shortcuts: a compact button that opens a small panel under it.
// Two kinds, each with its keyboard rules:
// - "dialog": a panel of status and buttons. Focus moves into it; Tab moves
//   through it as usual.
// - "menu": a list of actions. Arrow keys, Home and End move between them;
//   Tab closes it.
// Both close on Escape (focus goes back to the button) or a click outside.
import clsx from "clsx";
import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/ui";
import type { AnyIcon } from "@/components/ui/Icon";

/** The toolbar button's look: the GitHub entry's, so every shortcut matches it. */
export const TRIGGER =
  "flex shrink-0 cursor-pointer items-center gap-1.5 self-center rounded-[3px] px-1 py-1 font-sans xl:px-1.5 text-[13px] leading-none whitespace-nowrap transition-colors";

export const PANEL =
  "absolute top-full right-0 z-40 mt-1 rounded-[4px] border border-line bg-surface text-left text-ink shadow-[0_18px_40px_-18px_rgba(0,0,0,0.35)]";

/** A menu item's look. */
export const ITEM =
  "flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left font-sans text-[13px] text-ink hover:bg-sunken focus-visible:bg-sunken focus-visible:outline-offset-[-2px]";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function visible(el: HTMLElement): boolean {
  return el.checkVisibility?.() ?? true;
}

function menuItems(box: HTMLElement): HTMLElement[] {
  return [...box.querySelectorAll<HTMLElement>('[role^="menuitem"]')].filter(
    (el) => visible(el) && el.getAttribute("aria-disabled") !== "true",
  );
}

export interface ShortcutProps {
  /** What the button is, for screen readers, with how it stands ("Database: connected"). */
  label: string;
  /** The tooltip: the state, and what a click does. */
  title: string;
  icon: AnyIcon;
  /** Short words beside the icon when something needs attention ("Key missing"), from 2xl up. */
  attention?: string;
  /** Always-visible words beside the icon. */
  text?: string;
  /** The attention colour, for a button whose text is always a warning. */
  tone?: "attn";
  kind?: "dialog" | "menu";
  /** The panel's width class. */
  width?: string;
  className?: string;
  /** The panel's content; `close(false)` closes it without moving focus (a link that navigates). */
  children: (close: (refocus?: boolean) => void) => ReactNode;
}

export function Shortcut({
  label,
  title,
  icon,
  attention,
  text,
  tone,
  kind = "dialog",
  width = "w-80",
  className,
  children,
}: ShortcutProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };

  // Into the panel on open: a menu's first item, a dialog's first control.
  useEffect(() => {
    if (!open || !panel.current) return;
    const first =
      kind === "menu"
        ? menuItems(panel.current)[0]
        : [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].find(visible);
    (first ?? panel.current).focus();
  }, [open, kind]);

  // A click outside closes it, leaving focus where the click put it.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (kind !== "menu" || !panel.current) return;
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    const items = menuItems(panel.current);
    if (!items.length) return;
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next =
      event.key === "ArrowDown"
        ? items[(at + 1) % items.length]
        : event.key === "ArrowUp"
          ? items[(at - 1 + items.length) % items.length]
          : event.key === "Home"
            ? items[0]
            : event.key === "End"
              ? items[items.length - 1]
              : null;
    if (next) {
      event.preventDefault();
      next.focus();
    }
  };

  const onButtonKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    // A menu also opens with the arrow keys, as menus do.
    if (kind === "menu" && (event.key === "ArrowDown" || event.key === "ArrowUp") && !open) {
      event.preventDefault();
      setOpen(true);
    }
  };

  return (
    <div
      ref={wrap}
      className={clsx("relative flex shrink-0 self-stretch", className)}
      onKeyDown={onKeyDown}
      // Focus moving out (Tab past the end, a click elsewhere) closes it.
      onBlur={(event) => {
        const next = event.relatedTarget as Node | null;
        if (open && next && !wrap.current?.contains(next)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        aria-haspopup={kind === "menu" ? "menu" : "dialog"}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        title={title}
        onClick={() => setOpen((was) => !was)}
        onKeyDown={onButtonKey}
        className={clsx(
          TRIGGER,
          attention || tone === "attn" ? "text-attn hover:text-ink" : "text-muted hover:text-ink",
          open && "text-ink",
        )}
      >
        <Icon name={icon} size={15} className="shrink-0" />
        {/* The words from 2xl up; below, the header's attention menu says them (AttentionMenu). */}
        {attention ? <span className="hidden 2xl:inline">{attention}</span> : text && <span>{text}</span>}
      </button>
      {open && (
        <div
          ref={panel}
          id={id}
          role={kind === "menu" ? "menu" : "dialog"}
          aria-label={label}
          tabIndex={-1}
          className={clsx(PANEL, width, kind === "menu" ? "py-1" : "p-4")}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

/** A panel's heading: what it is, and how it stands, in words. */
export function PanelHead({ title, state, tone }: { title: string; state: string; tone?: "good" | "attn" }) {
  return (
    <div>
      <p className="dl-label">{title}</p>
      <p
        className={clsx(
          "mt-1 flex items-center gap-1 font-sans text-[13px] font-medium",
          tone === "good" && "text-data",
          tone === "attn" && "text-attn",
          !tone && "text-ink",
        )}
      >
        {tone && <Icon name={tone === "good" ? "check" : "alert"} size={13} className="shrink-0" />}
        <span className="first-letter:uppercase">{state}</span>
      </p>
    </div>
  );
}
