import { type ComponentProps, createContext, lazy, Suspense, use, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { OpenFileContext, workspaceFile } from "@/lib/files";

import { ExternalLink } from "./ExternalLink";
import { NumberSources, type SourceLinks } from "./NumberSources";
import { markNumbers, type NumberSource } from "./provenance";

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
 */
export function Markdown({ text, numbers }: { text: string; numbers?: AnswerNumbers }) {
  const openFile = use(OpenFileContext);
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
          pre({ children }) {
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
            // A workspace path written as code (`/work/outputs/report.html`) opens the file.
            const path = source.trim();
            const file =
              !className && !inBlock && openFile && /^\/[^\s*?]+[^/]$/.test(path) ? workspaceFile(path) : null;
            if (file && openFile) {
              return (
                <button
                  type="button"
                  onClick={() => openFile(file)}
                  title={`Open ${source.trim()}`}
                  className="rounded-[2px] bg-sunken px-1 font-mono text-[0.74em] text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
                >
                  {source.trim()}
                </button>
              );
            }
            return <code className={className}>{children}</code>;
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
                <button type="button" onClick={follow} className="text-accent underline" title={href}>
                  {children}
                </button>
              );
            }
            // Links to files in the container open them in DataLab's viewer.
            const file = href && openFile ? workspaceFile(href) : null;
            if (file && openFile) {
              return (
                <button type="button" onClick={() => openFile(file)} className="text-accent underline" title={href}>
                  {children}
                </button>
              );
            }
            // A link within the answer itself (a footnote) stays a link.
            if (href && /^#[\w-]+$/.test(href)) return <a href={href}>{children}</a>;
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
