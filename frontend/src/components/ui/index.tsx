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
    <span className="inline-flex items-center gap-1.5 font-serif text-[14.5px] text-data italic">
      <Icon name="lock" size={13} /> Data session — reads the database, no internet
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 font-serif text-[14.5px] text-research italic">
      <Icon name="globe" size={13} /> Research session — the internet, no study data
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
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-[#1a1916]/40 p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className={clsx(
          "flex max-h-full flex-col rounded-[4px] border border-line bg-surface shadow-[0_24px_60px_-20px_rgba(0,0,0,0.35)]",
          wide ? "h-[85vh] w-[min(90vw,72rem)]" : "w-[34rem]",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-line px-6 py-4">
          <h2 className="min-w-0 flex-1 truncate font-serif text-[21px] font-normal">{title}</h2>
          {actions}
          <Button variant="ghost" onClick={onClose} aria-label="Close" className="px-2">
            <Icon name="close" />
          </Button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">{children}</div>
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
    <div role="tablist" className="flex gap-4 overflow-x-auto border-b border-line px-4">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={clsx(
            "-mb-px border-b-2 py-2.5 font-sans text-[13px]",
            tab.id === value ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
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
      <p className="max-w-[20rem] font-sans text-[13px] leading-relaxed text-muted">{children}</p>
    </div>
  );
}
