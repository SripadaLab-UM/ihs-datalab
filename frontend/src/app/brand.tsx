import { useEffect } from "react";

/** DataLab's mark, "d.", as the header draws it. The geometry is
 * branding/build.py's (which makes the favicons and app icons); the colours
 * are the paper tokens, so it follows light and dark. Practice is the letter
 * on paper-cream with an amber period and edge, as its icons are. */
export function BrandMark({ practice = false, size = 22 }: { practice?: boolean; size?: number }) {
  const tile = practice ? "var(--color-attn-soft)" : "var(--color-ink)";
  const letter = practice ? "var(--color-ink)" : "var(--color-canvas)";
  const dot = practice ? "var(--color-attn)" : "var(--color-canvas)";
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="shrink-0">
      {practice ? (
        <rect x="1" y="1" width="62" height="62" rx="9" fill={tile} stroke="var(--color-attn)" strokeWidth="2" />
      ) : (
        <rect width="64" height="64" rx="10" fill={tile} />
      )}
      <circle cx="26" cy="38" r="10.5" fill="none" stroke={letter} strokeWidth="6.5" />
      <rect x="33.25" y="12" width="6.5" height="39.75" fill={letter} />
      <circle cx="47.5" cy="47.5" r="4.25" fill={dot} />
    </svg>
  );
}

/** The mark and the name, with a quiet "practice" beside them on practice.
 * Below `lg` only the mark, so the tabs fit (the badge still says practice). */
export function Brand({ practice }: { practice: boolean }) {
  return (
    <span
      className="flex shrink-0 items-center gap-2 py-3"
      data-testid="brand"
      title={practice ? "DataLab (practice)" : "DataLab"}
    >
      <BrandMark practice={practice} />
      <span className="hidden font-serif text-[20px] leading-none lg:inline">DataLab</span>
      {practice && (
        <span className="hidden font-serif text-[13px] leading-none text-attn italic lg:inline">practice</span>
      )}
    </span>
  );
}

/** On practice, the tab's icon and title say so too, so a practice tab
 * never looks like the real DataLab's. */
export function usePracticeTab(practice: boolean) {
  useEffect(() => {
    if (!practice) return;
    const links = [...document.querySelectorAll<HTMLLinkElement>("link[data-brand]")];
    const before = links.map((link) => link.getAttribute("href"));
    for (const link of links) {
      const href = link.getAttribute("href") ?? "";
      link.setAttribute("href", href.replace(/^\/(favicon|apple-touch-icon)/, "/$1-practice"));
    }
    const title = document.title;
    document.title = "DataLab (practice)";
    return () => {
      links.forEach((link, i) => link.setAttribute("href", before[i] ?? ""));
      document.title = title;
    };
  }, [practice]);
}
