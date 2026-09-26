import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

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
