import { useEffect } from "react";

import { type ArtPath, BLOCK_M, COLOURS, IHS_MARK, TILE } from "./brandArt";

/** The Block M, U-M's mark, on the icon's tile: Maize on Blue, and on
 * practice Blue on Maize, as its icons are. Never altered or merged with
 * another shape (the IHS mark sits beside it). The geometry is
 * branding/build.py's, which makes the favicons and app icons. */
export function BrandMark({ practice = false, size = 22 }: { practice?: boolean; size?: number }) {
  const [tile, m] = practice ? [COLOURS.maize, COLOURS.blue] : [COLOURS.blue, COLOURS.maize];
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="shrink-0">
      <rect width="64" height="64" rx={TILE.radius} fill={tile} />
      <path d={BLOCK_M.d} fill={m} transform={`translate(${TILE.x} ${TILE.y}) scale(${TILE.scale})`} />
    </svg>
  );
}

function Paths({ paths }: { paths: ArtPath[] }) {
  return paths.map((p, i) =>
    p.stroke === undefined ? (
      <path key={i} d={p.d} fill="currentColor" />
    ) : (
      <path
        key={i}
        d={p.d}
        fill="none"
        stroke="currentColor"
        strokeWidth={p.stroke}
        strokeLinecap={p.cap}
        strokeLinejoin="round"
      />
    ),
  );
}

/** The IHS + AI mark (branding/build.py's AI_MARK): U-M Blue on a light page
 * and Maize on a dark one, by the page's own color-scheme (light-dark(); a
 * browser without it draws it in the ink). */
export function IhsMark({ size = 11 }: { size?: number }) {
  return (
    <svg
      height={size}
      width={(size * IHS_MARK.width) / IHS_MARK.height}
      viewBox={`0 0 ${IHS_MARK.width} ${IHS_MARK.height}`}
      aria-hidden="true"
      className="shrink-0"
      style={{ color: `light-dark(${COLOURS.blue}, ${COLOURS.maize})` }}
      data-mark={IHS_MARK.name}
    >
      <Paths paths={IHS_MARK.paths} />
    </svg>
  );
}

/** The logo and the name, with a quiet "practice" beside them on practice.
 * Below `lg` only the Block M, so the tabs fit (the badge still says practice). */
export function Brand({ practice }: { practice: boolean }) {
  return (
    <span
      className="flex shrink-0 items-center gap-2 py-3"
      data-testid="brand"
      title={practice ? "DataLab (practice)" : "DataLab"}
    >
      <BrandMark practice={practice} />
      <span className="hidden lg:inline-flex">
        <IhsMark />
      </span>
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
