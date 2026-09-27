import type { ReactNode } from "react";

/** One section of the Settings page: an ink rule, a serif title, and what it's about.
 *  With `actions` (even an empty one), they sit on the title's line, to its right. */
export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const heading = <h2 className="font-serif text-[28px] leading-tight">{title}</h2>;
  return (
    <section className="border-t border-ink pt-6">
      {actions !== undefined ? (
        <div className="flex items-center justify-between">
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
