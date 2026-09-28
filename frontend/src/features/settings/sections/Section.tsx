import clsx from "clsx";
import type { ReactNode } from "react";

import { Icon } from "@/components/ui";

/** One section of the Settings page: an ink rule, a serif title, and what it's about.
 *  With `actions` (even an empty one), they sit on the title's line, to its right. */
export function Section({
  title,
  actions,
  id,
  children,
}: {
  title: string;
  actions?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  const heading = <h2 className="font-serif text-[28px] leading-tight">{title}</h2>;
  return (
    <section id={id} className="scroll-mt-6 border-t border-ink pt-6">
      {actions !== undefined ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          {heading}
          {actions}
        </div>
      ) : (
        heading
      )}
      {children}
    </section>
  );
}

/** A part of a section (U-M GPT, the database, GitHub): a hairline, a smaller serif title, its state and actions. */
export function Part({
  title,
  state,
  actions,
  id,
  className,
  children,
}: {
  title: string;
  /** How it stands, beside the title: "Saved in keychain", "Fixed on the practice DataLab"… */
  state?: ReactNode;
  actions?: ReactNode;
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-label={title} className={clsx("scroll-mt-6 border-t border-line pt-5", className)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="font-serif text-[21px] leading-tight">{title}</h3>
        {state}
        {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** "Fixed on the practice DataLab": a setting practice doesn't let you change, labelled rather than hidden. */
export function FixedOnPractice({ children }: { children?: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-[2px] border border-attn/40 px-1.5 font-sans text-[12px] leading-[19px] text-attn">
      <Icon name="lock" size={11} />
      {children ?? "Fixed on the practice DataLab"}
    </span>
  );
}
