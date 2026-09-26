import { lazy, Suspense, use } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { OpenFileContext, workspaceFile } from "@/lib/files";

import { ExternalLink } from "./ExternalLink";

const VegaChart = lazy(() => import("./VegaChart"));

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
          code({ className, children }) {
            const language = /language-(\S+)/.exec(className ?? "")?.[1];
            const source = String(children).replace(/\n$/, "");
            if (language === "vega-lite") {
              return (
                <Suspense fallback={<div className="text-sm text-muted">Drawing chart…</div>}>
                  <VegaChart spec={source} />
                </Suspense>
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
