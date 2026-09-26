import { lazy, Suspense } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const VegaChart = lazy(() => import("./VegaChart"));

/**
 * Renders an agent's Markdown. Charts in ```vega-lite blocks are drawn inline.
 * Images are only shown if they're embedded (data: URLs). Links to images
 * elsewhere are dropped: the page's security policy would block them anyway,
 * since loading one could send data out.
 */
export function Markdown({ text }: { text: string }) {
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
            return (
              <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
