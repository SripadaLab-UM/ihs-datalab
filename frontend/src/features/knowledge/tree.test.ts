import { describe, expect, it } from "vitest";

import { entry, FIXTURE } from "./tree.fixture";
import { ancestorsOf, buildTree, filterTree, searchWords, type TreeNode, visibleRows } from "./tree";

const shape = (nodes: TreeNode[]): unknown[] => nodes.map((n) => (n.children.length ? { [`${n.label} · ${n.count}`]: shape(n.children) } : n.label));

describe("buildTree", () => {
  it("groups the layout into sections, tables under the source they link to", () => {
    expect(shape(buildTree(FIXTURE))).toEqual([
      {
        "Study documentation · 4": ["index.md", "README.md", "AGENTS.md", { "Lab papers · 1": ["naps-and-mood"] }],
      },
      {
        "Data sources · 8": [
          "diary",
          { "ring · 1": ["IHS_2031.RINGSLEEP"] },
          // Two cohorts' schemas: by cohort, newest first.
          {
            "wristband · 2": [{ "IHS_2031 · 1": ["IHS_2031.BANDDAILY"] }, { "IHS_2030 · 1": ["IHS_2030.BANDDAILY"] }],
          },
          { "Tables without a source · 1": ["IHS_2031.LOOKUP"] },
          { "Catalog reports · 1": ["drift.md"] },
        ],
      },
      { "Analysis methods · 1": [{ "Derived features · 1": ["nap_minutes"] }] },
      { "Quality checks · 1": ["nap_window"] },
      { "Workflows and pipelines · 2": [{ "nap-check · 1": ["check.R"] }] },
    ]);
  });

  it("lists what no section takes under Other, by folder, so nothing is hidden", () => {
    const tree = buildTree(
      [...FIXTURE, entry("notes/stray.md", "page", "stray"), entry("CHANGES.md", "top")],
      [
        {
          key: "sources",
          title: "Data sources",
          parts: [{ folder: "sources" }],
        },
      ],
    );
    const other = tree.find((s) => s.label === "Other")!;
    expect(other.count).toBe(FIXTURE.length - 3 + 2);
    expect(other.children.find((c) => c.label === "notes/")?.children.map((c) => c.label)).toEqual(["stray"]);
    expect(other.children.find((c) => c.label === "tables/")?.count).toBe(4);
    // Every file is listed exactly once.
    const paths: string[] = [];
    const walk = (nodes: TreeNode[]) => nodes.forEach((n) => (n.entry && paths.push(n.entry.path), walk(n.children)));
    walk(tree);
    expect(paths.sort()).toEqual([...FIXTURE.map((e) => e.path), "notes/stray.md", "CHANGES.md"].sort());
  });

  it("leaves out empty sections", () => {
    expect(buildTree([entry("qc/nap_window.md", "page")]).map((s) => s.label)).toEqual(["Quality checks"]);
    expect(buildTree([])).toEqual([]);
  });
});

describe("finding pages", () => {
  const tree = buildTree(FIXTURE);

  it("knows what holds a page", () => {
    expect(ancestorsOf(tree, "tables/IHS_2030.BANDDAILY.md")).toEqual([
      "section:sources", "page:sources/wristband.md", "page:sources/wristband.md/IHS_2030",
    ]); // prettier-ignore
    expect(ancestorsOf(tree, "nowhere.md")).toEqual([]);
  });

  it("searches inside folded sections, on titles, paths, summaries and cohorts, keeping what holds a match", () => {
    const found = filterTree(tree, searchWords("Nap"));
    expect([...found.hits].sort()).toEqual([
      "page:features/nap_minutes.md", "page:papers/naps-and-mood.md", "page:qc/nap_window.md",
      "page:skills/nap-check/SKILL.md", "page:skills/nap-check/check.R",
    ]); // prettier-ignore
    expect(found.open.has("section:methods") && found.open.has("group:methods/features")).toBe(true);
    expect(filterTree(tree, searchWords("smart rings")).tree.map((s) => s.label)).toEqual(["Data sources"]);
    // A table found by its cohort: its source is shown to place it, and opened.
    const byCohort = filterTree(tree, searchWords("2031 sleep"));
    expect([...byCohort.hits]).toEqual(["page:tables/IHS_2031.RINGSLEEP.md"]);
    expect(byCohort.open.has("page:sources/ring.md")).toBe(true);
    expect(filterTree(tree, searchWords("zzz")).tree).toEqual([]);
  });

  it("lists the rows shown, given what's open", () => {
    const open = new Set(["section:sources", "page:sources/ring.md"]);
    expect(visibleRows(tree, (id) => open.has(id)).map((r) => `${r.level}:${r.node.label}`)).toEqual([
      "1:Study documentation", "1:Data sources", "2:diary", "2:ring", "3:IHS_2031.RINGSLEEP", "2:wristband",
      "2:Tables without a source", "2:Catalog reports", "1:Analysis methods", "1:Quality checks", "1:Workflows and pipelines",
    ]); // prettier-ignore
  });
});
