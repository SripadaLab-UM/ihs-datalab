// Written by branding/build.py: change the geometry there and run it again.

export type ArtPath = { d: string; stroke?: number; cap?: "butt" | "round" };

/** The Block M, the University's mark, as traced from the prototype. */
export const BLOCK_M = { width: 132, height: 104, d: "M0 0 46 0 66 52 86 0 132 0 132 24 119 24 119 82 132 82 132 104 81 104 81 82 92 82 92 30.5 72.5 82 59.5 82 40 30.5 40 82 51 82 51 104 0 104 0 82 13 82 13 24 0 24Z" };

/** The icon's tile as the header draws it, 64 units square (the 32 px icon): the
 * Block M and, over its top-right corner, the spark, with an edge knocked out of the
 * tile's colour (`halo`). Real: Maize on Blue; practice: Blue on Maize. */
export const TILE: { radius: number; x: number; y: number; scale: number; spark: { d: string; halo: number } | null } = { radius: 12, x: 12, y: 19.5, scale: 0.303, spark: { d: "M49.5 9.5Q50.76 18.74 60 20Q50.76 21.26 49.5 30.5Q48.24 21.26 39 20Q48.24 18.74 49.5 9.5Z", halo: 2.2 } };
export const COLOURS = { maize: "#FFCB05", blue: "#00274C" };

/** The IHS + AI mark ('spark' in branding/build.py). */
export const IHS_MARK: { name: string; width: number; height: number; paths: ArtPath[] } = {
  name: "spark",
  width: 47.75,
  height: 20,
  paths: [
    { d: "M0 0 4.5 0 4.5 20 0 20Z" },
    { d: "M7.5 0 12 0 12 20 7.5 20Z" },
    { d: "M17 0 21.5 0 21.5 20 17 20Z" },
    { d: "M7.5 7.75 21.5 7.75 21.5 12.25 7.5 12.25Z" },
    { d: "M30.625 10A3.875 3.875 0 1 1 33.981 4.187", stroke: 4.5, cap: "butt" },
    { d: "M30.625 10A3.875 3.875 0 1 1 27.269 15.812", stroke: 4.5, cap: "butt" },
    { d: "M43.25 0Q43.79 3.96 47.75 4.5Q43.79 5.04 43.25 9Q42.71 5.04 38.75 4.5Q42.71 3.96 43.25 0Z" },
  ],
};
