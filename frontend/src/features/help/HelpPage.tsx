import clsx from "clsx";
import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { Button, Icon } from "@/components/ui";
import { type GuidePage, helpPath, pageBySlug, PAGES, searchGuide } from "@/lib/guide";

import { GuideMarkdown } from "./GuideMarkdown";
import { tourSettings, useTour } from "./Tour";

// The contents, grouped by each page's `order` in its front matter.
const GROUPS: { title: string; from: number; to: number }[] = [
  { title: "Start", from: 0, to: 10 },
  { title: "Working with the agent", from: 10, to: 20 },
  { title: "The other tabs", from: 20, to: 30 },
  { title: "Setting up", from: 30, to: 40 },
  { title: "Reference", from: 40, to: 1000 },
];

// Where "Back" goes, named as the tabs are.
const PLACES: [string, string][] = [
  ["/workspace", "the Workspace"],
  ["/sql", "the SQL Playground"],
  ["/workflows", "Workflows"],
  ["/pipelines", "Pipelines"],
  ["/knowledge", "Knowledge"],
  ["/settings", "Settings & Safety"],
];

function placeName(path: string): string {
  return PLACES.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}#`))?.[1] ?? "where you were";
}

/** Help: the guide in docs/guide, bundled with the app, searchable, opened on the current screen's topic. */
export function HelpPage() {
  const { slug = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const tour = useTour();
  const page = pageBySlug(slug === "README" ? "" : slug);
  // Where Help was opened from, kept while moving between its pages.
  const opened = (location.state as { from?: string } | null)?.from;
  const [from, setFrom] = useState(opened);
  if (opened && opened !== from) setFrom(opened);
  const [query, setQuery] = useState("");
  const searchId = useId();
  const scroller = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);

  // A heading asked for (#rigor-review) is scrolled to and focused; otherwise the page's top.
  useEffect(() => {
    const id = location.hash ? safeDecode(location.hash.slice(1)) : "";
    const target = id ? document.getElementById(id) : null;
    if (target) {
      target.scrollIntoView?.({ block: "start" });
      target.focus({ preventScroll: true });
      return;
    }
    scroller.current?.scrollTo?.({ top: 0 });
    article.current?.querySelector<HTMLElement>("h1")?.focus({ preventScroll: true });
  }, [slug, location.hash]);

  const results = query.trim() ? searchGuide(query) : null;
  return (
    <div ref={scroller} className="h-full overflow-y-auto">
      <div className="mx-auto grid max-w-6xl gap-x-12 gap-y-8 px-5 py-8 sm:px-8 md:grid-cols-[15rem_minmax(0,1fr)] lg:py-12">
        <nav aria-label="Help" className="flex flex-col gap-6 md:sticky md:top-0 md:self-start">
          <div role="search">
            <label htmlFor={searchId} className="dl-label">
              Search Help
            </label>
            <div className="mt-2 flex items-center gap-2 rounded-[3px] border border-line bg-field px-2.5 focus-within:border-ink">
              <Icon name="search" size={14} className="shrink-0 text-faint" />
              <input
                id={searchId}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setQuery("")}
                placeholder="export, plan, replay…"
                className="min-w-0 flex-1 bg-transparent py-1.5 font-sans text-[13.5px] outline-none placeholder:text-faint"
              />
            </div>
          </div>
          {results ? <Results results={results} /> : <Contents current={page} />}
          {tourSettings.enabled && (
            <div className="border-t border-line pt-4">
              <Button
                onClick={() => {
                  tour.start();
                  navigate("/workspace");
                }}
              >
                Take the tour
              </Button>
              <p className="mt-2 font-sans text-[12px] leading-relaxed text-muted">
                Five steps through a first conversation. Best on the practice DataLab.
              </p>
            </div>
          )}
        </nav>

        <main ref={article} className="min-w-0 max-w-[46rem]">
          {from && (
            <Link to={from} className="mb-6 inline-flex items-center gap-1 font-sans text-[13px] text-muted hover:text-ink">
              <Icon name="chevron" size={13} className="rotate-180" /> Back to {placeName(from)}
            </Link>
          )}
          {page ? (
            <article className="[&_.prose-datalab_h1]:mt-0 [&_.prose-datalab_h1]:text-[2.1em] [&_.prose-datalab_h1]:leading-tight">
              <GuideMarkdown page={page} text={page.body} />
            </article>
          ) : (
            <div className="flex flex-col gap-3">
              <h1 tabIndex={-1} className="font-serif text-[30px] outline-none">
                There's no Help page here
              </h1>
              <p className="font-serif text-[17px] text-muted">
                <Link to="/help" className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
                  Start here
                </Link>{" "}
                for the list of guides, or search for what you need.
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function safeDecode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function Contents({ current }: { current?: GuidePage }) {
  return (
    <div className="flex flex-col gap-5">
      {GROUPS.map((group) => {
        const pages = PAGES.filter((p) => p.order >= group.from && p.order < group.to);
        if (pages.length === 0) return null;
        return (
          <section key={group.title}>
            <h2 className="dl-label mb-1.5">{group.title}</h2>
            <ul className="flex flex-col">
              {pages.map((p) => {
                const here = p === current;
                return (
                  <li key={p.file}>
                    <Link
                      to={helpPath(p)}
                      aria-current={here ? "page" : undefined}
                      className={clsx(
                        "block border-l-2 py-1 pl-3 font-sans text-[13.5px] leading-snug",
                        here ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
                      )}
                    >
                      {p.title}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function Results({ results }: { results: ReturnType<typeof searchGuide> }) {
  return (
    <section aria-label="Search results">
      <p role="status" className="dl-label mb-2">
        {results.length === 0 ? "Nothing found" : `${results.length} result${results.length === 1 ? "" : "s"}`}
      </p>
      {results.length === 0 ? (
        <p className="font-sans text-[13px] leading-relaxed text-muted">Try another word, or look through the contents.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line border-y border-line">
          {results.map((r) => (
            <li key={r.to}>
              <Link to={r.to} className="group block py-2.5">
                <span className="block font-sans text-[13.5px] font-medium text-ink group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">
                  {r.title}
                </span>
                <span className="block font-sans text-[11.5px] tracking-[0.08em] text-faint uppercase">{r.within}</span>
                <span className="mt-0.5 line-clamp-3 block font-sans text-[12.5px] leading-snug text-muted">{r.snippet}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
