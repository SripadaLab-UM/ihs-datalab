import { type ComponentProps, createContext, lazy, Suspense, use, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { FileGlyph, Icon } from "@/components/ui";
import { type AnswerFileLink, answerFileLinks, KnownFilesContext, OpenFileContext, QUERY_ID, workspaceFile, containerPath } from "@/lib/files";

import { ExternalLink } from "./ExternalLink";
import { NumberSources, type SourceLinks } from "./NumberSources";
import { markNumbers, type NumberSource, ShowQueryContext } from "./provenance";

/** An answer's numbers, each with where it appears, and how to open those places. */
export interface AnswerNumbers {
  sources: Map<string, NumberSource[]>;
  links: SourceLinks;
}

const VegaChart = lazy(() => import("./VegaChart"));
// Code inside a block (```) stays code, even on one line: only inline code opens files.
const InBlock = createContext(false);

/** Links the page beside the text follows itself, such as the Knowledge tab's links
 *  between pages: what to do when one is clicked, or null to treat it as any link. */
export const InternalLinks = createContext<((href: string) => (() => void) | null) | null>(null);

/**
 * Renders an agent's Markdown. Charts in ```vega-lite blocks are drawn inline.
 * Images are only shown if they're embedded (data: URLs). Links to images
 * elsewhere are dropped: the page's security policy would block them anyway,
 * since loading one could send data out. Other links open only after the
 * person has seen the full address and confirmed.
 *
 * A file of this conversation that the text names (`/work/outputs/report.html`)
 * shows as a button saying what it is ("Open report"), its path in the
 * tooltip; a path it doesn't know stays text. Query ids aren't shown: a known
 * one is a button to the Queries tab. In an `answer`, SQL blocks are folded
 * away too: the queries that ran are in the Queries tab.
 */
export function Markdown({ text, numbers, answer = false }: { text: string; numbers?: AnswerNumbers; answer?: boolean }) {
  const openFile = use(OpenFileContext);
  const known = use(KnownFilesContext);
  const files = useMemo(() => (known && openFile ? answerFileLinks(text, known) : new Map<string, AnswerFileLink>()), [text, known, openFile]);
  // The same plugin while the numbers are the same, however often the chat re-renders.
  const keys = numbers ? [...numbers.sources.keys()].join("\u0000") : "";
  const marked = useMemo(() => (keys ? [markNumbers(keys.split("\u0000"))] : []), [keys]);
  return (
    <NumbersContext value={numbers}>
    <div className="prose-datalab">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={marked}
        components={{
          // One component for good (not one per render), so an open popover stays open.
          span: NumberSpan,
          pre({ children, node }) {
            const language = sqlBlock(node);
            if (answer && language) {
              return (
                <details className="font-sans text-[13px] text-muted">
                  <summary className="hover:text-ink">SQL the answer quotes (the queries that ran are in the Queries tab)</summary>
                  <InBlock value={true}>
                    <pre>{children}</pre>
                  </InBlock>
                </details>
              );
            }
            return (
              <InBlock value={true}>
                <pre>{children}</pre>
              </InBlock>
            );
          },
          code({ className, children }) {
            // A component, so it may read context; `use` works here like a hook.
            const inBlock = use(InBlock);
            const language = /language-(\S+)/.exec(className ?? "")?.[1];
            const source = String(children).replace(/\n$/, "");
            if (language === "vega-lite") {
              return (
                <Suspense fallback={<div className="text-sm text-muted">Drawing chart…</div>}>
                  <VegaChart spec={source} />
                </Suspense>
              );
            }
            const inline = !className && !inBlock;
            // A file of this conversation written as code (`/work/outputs/report.html`) opens it.
            const path = source.trim();
            const named = inline ? workspaceFile(path) : null;
            const link = named ? files.get(containerOf(path)) : undefined;
            if (link && openFile) return <FileChip link={link} onOpen={openFile} />;
            if (inline && QUERY_ID.test(path)) return <QueryMention id={path} known={known} />;
            return <code className={className}>{children}</code>;
          },
          // A wide table scrolls sideways within its own block, never the page or the chat.
          table({ children }) {
            return (
              <div data-scroll-x className="dl-scroll-x">
                <table>{children}</table>
              </div>
            );
          },
          img({ src, alt }) {
            return typeof src === "string" && src.startsWith("data:image/") ? (
              <img src={src} alt={alt ?? ""} className="max-w-full rounded" />
            ) : (
              <span className="text-xs text-muted">[image not shown]</span>
            );
          },
          a({ href, children }) {
            const internal = use(InternalLinks);
            const follow = href && internal ? internal(href) : null;
            if (follow) {
              return (
                <button type="button" onClick={follow} className="text-accent underline decoration-faint underline-offset-[3px] hover:decoration-ink" title={href}>
                  {children}
                </button>
              );
            }
            // Links to this conversation's files open them in DataLab's viewer;
            // other container paths aren't links at all.
            const file = href ? workspaceFile(href) : null;
            if (file) {
              const link = files.get(containerOf(href ?? ""));
              if (!link || !openFile) return <span title={href}>{children}</span>;
              return (
                <button type="button" onClick={() => openFile(link.file)} className="text-accent underline decoration-faint underline-offset-[3px] hover:decoration-ink" title={link.path}>
                  {children}
                </button>
              );
            }
            // A link within the answer itself (a footnote) stays a link.
            if (href && /^#[\w-]+$/.test(href)) return <a href={href} className="hover:decoration-ink">{children}</a>;
            return <ExternalLink href={href}>{children}</ExternalLink>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
    </NumbersContext>
  );
}

// The answer's numbers, for the spans markNumbers made.
const NumbersContext = createContext<AnswerNumbers | undefined>(undefined);

/** A span in an answer: a number the answer states is shown with where it appears. */
function NumberSpan({ node: _node, ...props }: ComponentProps<"span"> & { node?: unknown }) {
  const numbers = use(NumbersContext);
  const number = (props as Record<string, unknown>)["data-number"];
  if (numbers && typeof number === "string") {
    return <NumberSources text={number} sources={numbers.sources.get(number) ?? []} links={numbers.links} />;
  }
  return <span {...props} />;
}

/** A container path as `answerFileLinks` keys it. */
function containerOf(raw: string): string {
  const file = workspaceFile(raw.trim());
  return file ? containerPath(file) : "";
}

/** Whether a <pre> holds a ```sql block. */
function sqlBlock(node: unknown): boolean {
  const code = (node as { children?: { tagName?: string; properties?: { className?: unknown } }[] } | undefined)?.children?.[0];
  const names = code?.tagName === "code" ? code.properties?.className : undefined;
  return Array.isArray(names) && names.some((name) => /^language-(sql|plsql|oracle)$/i.test(String(name)));
}

/** A file the answer names, as what it is ("Open report"); the path is in the tooltip. */
function FileChip({ link, onOpen }: { link: AnswerFileLink; onOpen: (file: AnswerFileLink["file"]) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(link.file)}
      title={link.path}
      className="inline-flex translate-y-[-1px] items-center gap-1.5 rounded-[3px] border border-line bg-surface py-px pr-2 pl-1 align-baseline font-sans text-[0.72em] leading-[1.5] text-ink hover:border-ink"
    >
      <FileGlyph kind={link.file.kind} size={15} />
      {link.label}
    </button>
  );
}

/** A query id in the text: a button to it in the Queries tab when it's this conversation's, else just "query". */
function QueryMention({ id, known }: { id: string; known: ReadonlySet<string> | null }) {
  const openQuery = use(ShowQueryContext);
  if (openQuery && known?.has(`/data/oracle/${id}.csv`)) {
    return (
      <button
        type="button"
        onClick={() => openQuery(id)}
        title={`Query ${id}: show it in the Queries tab`}
        className="inline-flex translate-y-[-1px] items-center gap-1 rounded-[3px] border border-line bg-surface px-1.5 py-px align-baseline font-sans text-[0.72em] leading-[1.5] text-ink hover:border-ink"
      >
        <Icon name="db" size={12} /> Show query
      </button>
    );
  }
  return <span title={`Query ${id}`}>a query</span>;
}
