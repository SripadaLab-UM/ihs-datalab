import clsx from "clsx";
import { type ButtonHTMLAttributes, type ReactNode, useEffect } from "react";

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  return (
    <button
      {...props}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-accent text-white hover:opacity-90",
        variant === "secondary" && "border border-line bg-surface hover:bg-sunken",
        variant === "danger" && "border border-line bg-surface text-danger hover:bg-sunken",
        variant === "ghost" && "text-muted hover:bg-sunken hover:text-ink",
        className,
      )}
    />
  );
}

/** Which kind of session this is: always visible, so nobody is unsure. */
export function SessionBadge({ kind }: { kind: "data" | "research" }) {
  return kind === "data" ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-data-soft px-2 py-0.5 text-xs font-medium text-data">
      🔒 Data session · no internet
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-research-soft px-2 py-0.5 text-xs font-medium text-research">
      🌐 Research session · no study data
    </span>
  );
}

export function Panel({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col">
      <header className="flex items-center justify-between px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
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
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className={clsx(
          "flex max-h-full flex-col rounded-2xl bg-surface shadow-xl",
          wide ? "h-[85vh] w-[min(90vw,72rem)]" : "w-[34rem]",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-line px-5 py-3">
          <h2 className="min-w-0 flex-1 truncate font-semibold">{title}</h2>
          {actions}
          <Button variant="ghost" onClick={onClose} aria-label="Close">
            ✕
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
    <div role="tablist" className="flex gap-1 border-b border-line px-2">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={clsx(
            "border-b-2 px-2 py-2 text-xs font-medium",
            tab.id === value ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
