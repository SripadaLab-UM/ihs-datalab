import clsx from "clsx";
import { type ButtonHTMLAttributes, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { type AnyIcon, Icon } from "./Icon";

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  return (
    <button
      {...props}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-[3px] px-3 py-1.5 font-sans text-[13.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
        variant === "primary" && "bg-accent text-accent-ink hover:opacity-85",
        variant === "secondary" && "border border-line bg-transparent text-ink hover:border-ink",
        variant === "danger" && "border border-danger/40 bg-transparent text-danger hover:border-danger",
        variant === "ghost" && "text-muted hover:text-ink",
        className,
      )}
    />
  );
}

/** A small label for what something is or how it went. */
export function Chip({
  children,
  tone,
  title,
}: {
  children: ReactNode;
  tone?: "good" | "attn" | "bad" | "you";
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex max-w-full items-center gap-1 overflow-hidden rounded-[2px] border px-1.5 font-mono text-[11.5px] leading-[19px] whitespace-nowrap",
        !tone && "border-line text-muted",
        tone === "good" && "border-data/35 text-data",
        tone === "attn" && "border-attn/40 text-attn",
        tone === "bad" && "border-danger/40 text-danger",
        tone === "you" && "border-you/40 text-you",
      )}
    >
      {children}
    </span>
  );
}

/** Which kind of session this is: always visible, so nobody is unsure. */
export function SessionBadge({ kind }: { kind: "data" | "research" }) {
  return kind === "data" ? (
    <span
      className="inline-flex items-center gap-1.5 font-sans text-[13px] font-medium text-data"
      title="The agent can query the study database, read-only. It can't reach websites; its model runs on U-M's approved GPT service."
    >
      <Icon name="lock" size={13} /> Data session · database access, web blocked
    </span>
  ) : (
    <span
      className="inline-flex items-center gap-1.5 font-sans text-[13px] font-medium text-research"
      title="The agent can use the web. It has no connection to the study database and none of its data folders. Anything you attach may reach the web."
    >
      <Icon name="globe" size={13} /> Research session · web access, no database connection
    </span>
  );
}

export function Panel({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col">
      <header className="flex items-center justify-between px-5 py-2">
        <h2 className="dl-label">{title}</h2>
        {actions}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">{children}</div>
    </section>
  );
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';
function focusables(box: HTMLElement): HTMLElement[] {
  return [...box.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.checkVisibility?.() ?? true);
}

// Open dialogs, newest last: Escape closes only the top one.
const openDialogs: object[] = [];

/**
 * A dialog over the page. Focus moves into it and stays there, the page
 * behind is inert, and focus goes back where it was on close. Closes on
 * Escape or a click outside.
 */
export function Modal({
  title,
  onClose,
  children,
  actions,
  wide = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  // Where focus was before the dialog, read while rendering: a field inside it
  // with autoFocus takes focus before any effect runs.
  const [previous] = useState(() => (document.activeElement instanceof HTMLElement ? document.activeElement : null));
  // The control that gets focus on open, chosen once: StrictMode's second run
  // of the effect must not move it (from an autoFocus field to Close).
  const initial = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const root = document.getElementById("root");
    const me = {};
    openDialogs.push(me);
    root?.setAttribute("inert", "");
    // A field the dialog focused itself (autoFocus); otherwise the first visible control.
    if (!initial.current && box.current) {
      const active = document.activeElement;
      initial.current =
        active instanceof HTMLElement && box.current.contains(active)
          ? active
          : (focusables(box.current)[0] ?? box.current);
    }
    initial.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && openDialogs.at(-1) === me && close.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      openDialogs.splice(openDialogs.indexOf(me), 1);
      if (openDialogs.length === 0) root?.removeAttribute("inert");
      if (previous?.isConnected) previous.focus();
    };
  }, [previous]);
  const trapTab = (event: React.KeyboardEvent) => {
    if (event.key !== "Tab" || !box.current) return;
    const items = focusables(box.current);
    if (items.length === 0) return event.preventDefault();
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return createPortal(
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-[#1a1916]/40 p-6" onClick={onClose}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={trapTab}
        className={clsx(
          "flex max-h-full flex-col rounded-[4px] border border-line bg-surface shadow-[0_24px_60px_-20px_rgba(0,0,0,0.35)] outline-none",
          wide ? "h-[85vh] w-[min(90vw,72rem)]" : "w-[min(34rem,100%)]",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-line px-6 py-4">
          <h2 id={titleId} className="min-w-0 flex-1 truncate font-serif text-[21px] font-normal">
            {title}
          </h2>
          {actions}
          <Button variant="ghost" onClick={onClose} aria-label="Close" className="px-2">
            <Icon name="close" />
          </Button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-4 overflow-x-auto border-b border-line px-4">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={clsx(
            "-mb-px border-b py-3 font-sans text-[10.5px] font-semibold tracking-[0.14em] uppercase",
            tab.id === value ? "border-ink text-ink" : "border-transparent text-faint hover:text-ink",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export { Icon } from "./Icon";

const KIND_ICON: Record<string, AnyIcon> = {
  html: "page",
  image: "image",
  csv: "table",
  pdf: "page",
  text: "file",
  folder: "folder",
};

/** A small tile that says what kind of file this is, at a glance. */
export function FileGlyph({ kind, size = 32 }: { kind: string; size?: number }) {
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-[2px] border border-line text-muted"
      style={{ width: size, height: size }}
    >
      <Icon name={KIND_ICON[kind] ?? "file"} size={Math.round(size * 0.5)} />
    </span>
  );
}

/** What a panel says when there is nothing in it yet: what will appear, and when. */
export function EmptyNote({ icon, title, children }: { icon: AnyIcon; title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 border-t border-line pt-4">
      <p className="dl-label flex items-center gap-1.5">
        <Icon name={icon} size={12} /> {title}
      </p>
      <p className="max-w-[20rem] font-serif text-[15px] leading-relaxed text-muted italic">{children}</p>
    </div>
  );
}
