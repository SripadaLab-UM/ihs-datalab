import { createContext, lazy, Suspense, use } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { OpenFileContext, workspaceFile } from "@/lib/files";

import { ExternalLink } from "./ExternalLink";

const VegaChart = lazy(() => import("./VegaChart"));
// Code inside a block (```) stays code, even on one line: only inline code opens files.
const InBlock = createContext(false);

/**
 * Renders an agent's Markdown. Charts in ```vega-lite blocks are drawn inline.
 * Images are only shown if they're embedded (data: URLs). Links to images
 * elsewhere are dropped: the page's security policy would block them anyway,
 * since loading one could send data out. Other links open only after the
 * person has seen the full address and confirmed.
 */
export function Markdown({ text }: { text: string }) {
  const openFile = use(OpenFileContext);
  return (
    <div className="prose-datalab">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
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
  );
}
