import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { TOOLTIP_TERMS } from "@/components/ui/InfoTip";
import { TOUR_ANCHORS, tourSteps } from "@/features/help/Tour";

import { GLOSSARY, glossaryTerm, headings, PAGES, pageBySlug, parseGlossary, parsePage, plain, resolveGuideLink, searchGuide, slug, topicFor } from "./guide";

const GUIDE = resolve(__dirname, "../../../docs/guide");
const SRC = resolve(__dirname, "..");

describe("the guide, bundled", () => {
  it("has every page in docs/guide, each with a title and a summary", () => {
    const files = readdirSync(GUIDE).filter((f) => f.endsWith(".md")).sort();
    expect(PAGES.map((p) => p.file).sort()).toEqual(files);
    for (const page of PAGES) {
      expect(page.title, page.file).not.toBe("");
      expect(page.summary, page.file).not.toBe("");
      // The bundled text is the file's, word for word.
      expect(readFileSync(join(GUIDE, page.file), "utf8")).toContain(page.body.trim());
    }
  });

  it("links only to guide pages and headings that exist", () => {
    for (const page of PAGES) {
      for (const [, href] of page.body.matchAll(/\]\(([^)\s]+)\)/g)) {
        const to = resolveGuideLink(href, page);
        expect(to, `${page.file} links to ${href}, outside the guide`).not.toBeNull();
        const [path, anchor] = to!.split("#");
        const target = pageBySlug(path.replace(/^\/help\/?/, "") || "README")!;
        expect(target, `${page.file}: ${href}`).toBeDefined();
        if (anchor) expect(headings(target.body).map((h) => h.id), `${page.file}: ${href}`).toContain(anchor);
      }
    }
  });

  it("has the glossary terms help and onboarding need, each with a short first paragraph", () => {
    const needed = [
      "Data session",
      "Research session",
      "Plan",
      "Pilot",
      "Rigor review",
      "Trace and provenance",
      "Checkpoint",
      "Read-only",
      "Practice",
      "Proposal",
      "Save & share",
      "Workflow",
      "Pipeline",
      "Replay",
      "Small cells",
      "Data accessed",
    ];
    expect(GLOSSARY.map((t) => t.term)).toEqual(expect.arrayContaining(needed));
    for (const term of GLOSSARY) {
      expect(term.short.length, term.term).toBeGreaterThan(40);
      expect(term.short.length, `${term.term}'s tooltip is too long`).toBeLessThanOrEqual(300);
    }
  });

  it("is listed in docs/USER_GUIDE.md, page for page, in Help's order", () => {
    const guide = readFileSync(resolve(GUIDE, "../USER_GUIDE.md"), "utf8");
    const listed = [...guide.matchAll(/^\d+\. \[[^\]]+\]\(guide\/([\w-]+\.md)\)$/gm)].map((m) => m[1]);
    expect(listed).toEqual(PAGES.map((p) => p.file));
  });

  it("gives each screen its topic, and the start page otherwise", () => {
    expect(topicFor("/workspace").file).toBe("first-question.md");
    expect(topicFor("/workspace/c_123").file).toBe("reading-an-answer.md");
    expect(topicFor("/sql").file).toBe("sql-playground.md");
    expect(topicFor("/workflows/runs/r1").file).toBe("running-a-workflow.md");
    expect(topicFor("/pipelines").file).toBe("pipelines.md");
    expect(topicFor("/knowledge/tables/x").file).toBe("knowledge-proposals.md");
    expect(topicFor("/settings").slug).not.toBe("");
    expect(topicFor("/nowhere").file).toBe("README.md");
    // Every screen named in the guide is one of the app's.
    const routes = ["/workspace", "/sql", "/workflows", "/pipelines", "/knowledge", "/settings"];
    for (const page of PAGES) for (const screen of page.screens) expect(routes).toContain(screen.replace(/\/\*$/, ""));
  });
});

describe("tooltips", () => {
  it("use only terms the glossary has", () => {
    for (const term of TOOLTIP_TERMS) expect(glossaryTerm(term), term).toBeDefined();
  });

  it("name only terms the glossary has, wherever they're used", () => {
    const used: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) walk(path);
        else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) {
          for (const [, term] of readFileSync(path, "utf8").matchAll(/<InfoTip\b[^>]*?\bterm="([^"]+)"/g)) used.push(term);
        }
      }
    };
    walk(SRC);
    expect(used.length).toBeGreaterThan(5);
    for (const term of used) expect(glossaryTerm(term), `<InfoTip term="${term}">`).toBeDefined();
  });
});

describe("the tour", () => {
  it("has about five steps, each pointing at a place on screen", () => {
    const steps = tourSteps();
    expect(steps.length).toBe(5);
    expect(steps.map((s) => s.id).sort()).toEqual(Object.keys(TOUR_ANCHORS).sort());
    for (const step of steps) expect(step.text.length).toBeGreaterThan(40);
  });
});

describe("parsing", () => {
  it("makes GitHub's anchors", () => {
    expect(slug("Save & share")).toBe("save--share");
    expect(slug("Read an answer, its trace, and “How was this made?”")).toBe("read-an-answer-its-trace-and-how-was-this-made");
    expect(headings("## A\n```\n## not a heading\n```\n## A").map((h) => h.id)).toEqual(["a", "a-1"]);
  });

  it("reads front matter, and a term's first paragraph as plain text", () => {
    const page = parsePage("x/README.md", "---\ntitle: T\nsummary: S\norder: 3\nscreens: /a, /b/*\n---\n\n# Heading\n");
    expect(page).toMatchObject({ slug: "", title: "T", summary: "S", order: 3, screens: ["/a", "/b/*"] });
    const [term] = parseGlossary("## Plan\n\nA **plan** you [approve](plans.md), with `code`.\n\nMore.");
    expect(term).toMatchObject({ id: "plan", term: "Plan", short: "A plan you approve, with code." });
    expect(plain("*one* and _two_")).toBe("one and two");
  });
});

describe("search", () => {
  it("finds pages and terms by any of their words, the best match first", () => {
    const results = searchGuide("replay");
    expect(results[0]).toMatchObject({ title: "Replay", to: "/help/glossary#replay" });
    expect(results.map((r) => r.title)).toContain("Run a workflow");
    expect(searchGuide("export dropbox").map((r) => r.title)).toContain("Export results");
    expect(searchGuide("zzzz")).toEqual([]);
    expect(searchGuide(" ")).toEqual([]);
  });
});
