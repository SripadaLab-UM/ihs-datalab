// The user guide, docs/guide/*.md, bundled into the app when it's built: the
// one source of words for Help, the tooltips and the tour. The browser may
// only contact DataLab, so nothing here is fetched.

const RAW = import.meta.glob("../../../docs/guide/*.md", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

export interface GuidePage {
  /** The file's name without `.md`; "" for README.md, the start page. */
  slug: string;
  file: string;
  title: string;
  summary: string;
  order: number;
  /** Screens this page is the Help topic for: "/sql" exactly, or "/sql/*" below it. */
  screens: string[];
  keywords: string[];
  /** The Markdown after the front matter. */
  body: string;
}

export interface GlossaryTerm {
  /** The heading's anchor, as GitHub makes it: "save--share" for "Save & share". */
  id: string;
  term: string;
  /** The first paragraph: the tooltip's text, as plain text. */
  short: string;
  /** Everything under the heading, as Markdown. */
  body: string;
}

/** A heading's anchor, the way GitHub makes it, so links work there and in Help alike. */
export function slug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, "")
    .replace(/\s/g, "-");
}

/** Markdown inline text as plain text: links keep their words, emphasis and code marks go. */
export function plain(markdown: string): string {
  return markdown
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\*\*|__|`/g, "")
    .replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|[.,;:!?)]|$)/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

function frontMatter(text: string): { meta: Record<string, string>; body: string } {
  const match = /^---\n([\s\S]*?)\n---\n?/.exec(text);
  if (!match) return { meta: {}, body: text };
  const meta: Record<string, string> = {};
  for (const line of match[1].split("\n")) {
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    meta[line.slice(0, colon).trim()] = line
      .slice(colon + 1)
      .trim()
      .replace(/^"(.*)"$/, "$1");
  }
  return { meta, body: text.slice(match[0].length) };
}

const list = (value: string | undefined) =>
  (value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

/** Parses one guide file. Exported for tests. */
export function parsePage(file: string, text: string): GuidePage {
  const { meta, body } = frontMatter(text.replace(/\r\n/g, "\n"));
  const name = file.replace(/^.*\//, "").replace(/\.md$/, "");
  const heading = /^# (.+)$/m.exec(body)?.[1];
  return {
    slug: name === "README" ? "" : name,
    file: `${name}.md`,
    title: meta.title ?? heading ?? name,
    summary: meta.summary ?? "",
    order: Number(meta.order ?? 50),
    screens: list(meta.screens),
    keywords: list(meta.keywords),
    body,
  };
}

/** The terms in a glossary page: each `## Term`, with its first paragraph as the short text. */
export function parseGlossary(body: string): GlossaryTerm[] {
  const parts = body.split(/^## (.+)$/m);
  const terms: GlossaryTerm[] = [];
  for (let i = 1; i < parts.length; i += 2) {
    const term = parts[i].trim();
    const text = parts[i + 1].trim();
    const first = text.split(/\n\s*\n/)[0] ?? "";
    terms.push({ id: slug(term), term, short: plain(first), body: text });
  }
  return terms;
}

export const PAGES: GuidePage[] = Object.entries(RAW)
  .map(([file, text]) => parsePage(file, text))
  .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));

export const GLOSSARY: GlossaryTerm[] = parseGlossary(PAGES.find((p) => p.slug === "glossary")?.body ?? "");

const TERMS = new Map(GLOSSARY.map((t) => [t.id, t]));

export function glossaryTerm(id: string): GlossaryTerm | undefined {
  return TERMS.get(id);
}

export function pageBySlug(slugOrFile: string): GuidePage | undefined {
  const name = slugOrFile.replace(/\.md$/, "");
  return PAGES.find((p) => p.slug === name || (name === "README" && p.slug === ""));
}

/** Where a page is in Help. */
export function helpPath(page: Pick<GuidePage, "slug">, anchor?: string): string {
  return `/help${page.slug ? `/${page.slug}` : ""}${anchor ? `#${anchor}` : ""}`;
}

/**
 * A link written in a guide page, as a place in Help: another page
 * (`plans.md`, `glossary.md#plan`) or a heading on this one (`#pilots`).
 * Null for anything else, which isn't part of the guide.
 */
export function resolveGuideLink(href: string, from: Pick<GuidePage, "slug">): string | null {
  if (/^#[\w-]+$/.test(href)) return helpPath(from, href.slice(1));
  const match = /^(?:\.\/)?([\w-]+)\.md(?:#([\w-]+))?$/.exec(href);
  if (!match) return null;
  const page = pageBySlug(match[1]);
  return page ? helpPath(page, match[2]) : null;
}

/** The Help topic for a screen: the page naming it most closely, else the start page. */
export function topicFor(pathname: string): GuidePage {
  const path = pathname.replace(/\/+$/, "") || "/";
  let best: { page: GuidePage; length: number } | null = null;
  for (const page of PAGES) {
    for (const screen of page.screens) {
      const below = screen.endsWith("/*");
      const base = below ? screen.slice(0, -2) : screen;
      const hit = below ? path.startsWith(`${base}/`) : path === base;
      if (hit && (!best || base.length + (below ? 0 : 1) > best.length)) {
        best = { page, length: base.length + (below ? 0 : 1) };
      }
    }
  }
  return best?.page ?? PAGES.find((p) => p.slug === "")!;
}

/** A page's headings, `#` to `###`, with their anchors and (1-based) lines. */
export function headings(body: string): { level: number; text: string; id: string; line: number }[] {
  const seen = new Map<string, number>();
  const found: { level: number; text: string; id: string; line: number }[] = [];
  let fenced = false;
  body.split("\n").forEach((line, index) => {
    if (line.startsWith("```")) fenced = !fenced;
    const match = !fenced && /^(#{1,3}) (.+)$/.exec(line);
    if (!match) return;
    const text = plain(match[2]);
    found.push({ level: match[1].length, text, id: uniqueSlug(text, seen), line: index + 1 });
  });
  return found;
}

/** GitHub's rule for a repeated heading: the second gets "-1", and so on. */
export function uniqueSlug(text: string, seen: Map<string, number>): string {
  const base = slug(text);
  const count = seen.get(base) ?? 0;
  seen.set(base, count + 1);
  return count ? `${base}-${count}` : base;
}

export interface SearchResult {
  to: string;
  title: string;
  /** What it is: the page's title for a term or section, or "Guide". */
  within: string;
  snippet: string;
  score: number;
}

const bodyText = (markdown: string) =>
  plain(
    markdown
      .replace(/^---[\s\S]*?---/, "")
      .replace(/^#+ /gm, "")
      .replace(/^\s*[-*] |^\s*\d+\. /gm, ""),
  );

function snippet(text: string, words: string[]): string {
  const lower = text.toLowerCase();
  const at = Math.min(...words.map((w) => lower.indexOf(w)).filter((i) => i >= 0), text.length);
  let start = at >= text.length ? 0 : Math.max(0, at - 60);
  // From the start of a word.
  if (start > 0) start = text.indexOf(" ", start) + 1 || start;
  const cut = text.slice(start, start + 170);
  return `${start > 0 ? "…" : ""}${cut}${start + 170 < text.length ? "…" : ""}`;
}

interface Entry {
  to: string;
  title: string;
  within: string;
  fields: [string, number][];
  text: string;
}

const ENTRIES: Entry[] = [
  ...PAGES.filter((p) => p.slug !== "glossary").map((page) => ({
    to: helpPath(page),
    title: page.title,
    within: "Guide",
    fields: [
      [page.title, 6],
      [page.keywords.join(" "), 4],
      [page.summary, 3],
      [headings(page.body).map((h) => h.text).join(" "), 2],
    ] as [string, number][],
    text: bodyText(page.body),
  })),
  ...GLOSSARY.map((term) => ({
    to: helpPath({ slug: "glossary" }, term.id),
    title: term.term,
    within: "Glossary",
    fields: [
      [term.term, 7],
      [term.short, 3],
    ] as [string, number][],
    text: bodyText(term.body),
  })),
];

/** Searches the guide and the glossary: every word must appear somewhere; titles count most. */
export function searchGuide(query: string): SearchResult[] {
  const words = query
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length > 1);
  if (words.length === 0) return [];
  const results: SearchResult[] = [];
  for (const entry of ENTRIES) {
    let score = 0;
    let all = true;
    for (const word of words) {
      let best = entry.text.toLowerCase().includes(word) ? 1 : 0;
      for (const [field, weight] of entry.fields) if (field.toLowerCase().includes(word)) best = Math.max(best, weight);
      if (!best) all = false;
      score += best;
    }
    if (all) results.push({ to: entry.to, title: entry.title, within: entry.within, snippet: snippet(entry.text, words), score });
  }
  return results.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title)).slice(0, 20);
}
