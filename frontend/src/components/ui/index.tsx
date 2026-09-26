import clsx from "clsx";
import { type ButtonHTMLAttributes, type ReactNode, useEffect } from "react";

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
        "inline-flex items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-accent text-accent-ink hover:brightness-110",
        variant === "secondary" && "border border-line bg-surface text-ink hover:bg-sunken",
        variant === "danger" && "border border-danger/40 bg-surface text-danger hover:bg-danger-soft",
        variant === "ghost" && "text-muted hover:bg-sunken hover:text-ink",
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
        "inline-flex max-w-full items-center gap-1 truncate rounded-md px-2 py-0.5 font-mono text-[12px] leading-5",
        !tone && "border border-line bg-surface text-muted",
        tone === "good" && "bg-accent-soft text-accent",
        tone === "attn" && "bg-attn-soft text-attn",
        tone === "bad" && "bg-danger-soft text-danger",
        tone === "you" && "bg-you-soft text-you",
      )}
    >
      {children}
    </span>
  );
}

/** Which kind of session this is: always visible, so nobody is unsure. */
export function SessionBadge({ kind }: { kind: "data" | "research" }) {
  return kind === "data" ? (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-data-soft px-2.5 py-0.5 text-xs font-medium text-data">
      <Icon name="lock" size={13} /> Data session · no internet
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-research-soft px-2.5 py-0.5 text-xs font-medium text-research">
      <Icon name="globe" size={13} /> Research session · no study data
    </span>
  );
}

export function Panel({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col">
      <header className="flex items-center justify-between px-3 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">{title}</h2>
        {actions}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">{children}</div>
    </section>
  );
}

/** A dialog over the page. Closes on Escape or a click outside. */
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
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/45 p-6 backdrop-blur-[2px]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className={clsx(
          "flex max-h-full flex-col rounded-2xl border border-line bg-surface shadow-2xl",
          wide ? "h-[85vh] w-[min(90vw,72rem)]" : "w-[34rem]",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-line px-5 py-3">
          <h2 className="min-w-0 flex-1 truncate font-semibold">{title}</h2>
          {actions}
          <Button variant="ghost" onClick={onClose} aria-label="Close" className="px-2">
            <Icon name="close" />
          </Button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-5">{children}</div>
      </div>
    </div>
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
    <div role="tablist" className="flex gap-1 border-b border-line px-3">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={clsx(
            "-mb-px border-b-2 px-2 py-2.5 text-[13px] font-medium",
            tab.id === value ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export { Icon } from "./Icon";

const KIND_LOOK: Record<string, { icon: AnyIcon; className: string }> = {
  html: { icon: "page", className: "bg-accent-soft text-accent" },
  image: { icon: "image", className: "bg-you-soft text-you" },
  csv: { icon: "table", className: "bg-data-soft text-data" },
  pdf: { icon: "page", className: "bg-danger-soft text-danger" },
  text: { icon: "file", className: "bg-sunken text-muted" },
  folder: { icon: "folder", className: "bg-attn-soft text-attn" },
};

/** A small tile that says what kind of file this is, at a glance. */
export function FileGlyph({ kind, size = 32 }: { kind: string; size?: number }) {
  const look = KIND_LOOK[kind] ?? { icon: "file" as const, className: "bg-sunken text-muted" };
  return (
    <span
      aria-hidden
      className={clsx("inline-flex shrink-0 items-center justify-center rounded-lg", look.className)}
      style={{ width: size, height: size }}
    >
      <Icon name={look.icon} size={Math.round(size * 0.5)} />
    </span>
  );
}

/** What a panel says when there is nothing in it yet: what will appear, and when. */
export function EmptyNote({ icon, title, children }: { icon: AnyIcon; title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line px-4 py-6 text-center">
      <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-sunken text-faint">
        <Icon name={icon} size={18} />
      </span>
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-[18rem] text-xs text-muted">{children}</p>
    </div>
  );
}
