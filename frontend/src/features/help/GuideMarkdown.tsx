import { type ReactNode, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";

import { ExternalLink } from "@/components/chat/ExternalLink";
import { HighlightedCode } from "@/components/code/CodeBlock";
import { codeLanguage } from "@/components/code/languages";
import { type GuidePage, headings, resolveGuideLink } from "@/lib/guide";

type Positioned = { position?: { start?: { line?: number } } };

/**
 * A guide page, rendered. The guide is DataLab's own text, but it's rendered
 * as the agent's answers are: no raw HTML, no outside images, and a link that
 * isn't to another guide page opens only after the person has seen where it
 * goes. Headings get GitHub's anchors, so links into them work here and on
 * GitHub alike.
 */
export function GuideMarkdown({
  page,
  text,
  headingOffset = 0,
  onNavigate,
}: {
  page: Pick<GuidePage, "slug">;
  text: string;
  /** Renders `#` as `h2` with 1, for text inside something with its own title. */
  headingOffset?: number;
  onNavigate?: () => void;
}) {
  const components = useMemo<Components>(() => {
    const anchors = new Map(headings(text).map((h) => [h.line, h.id]));
    const heading =
      (level: number) =>
      ({ node, children }: { node?: Positioned; children?: ReactNode }) => {
        const Tag = `h${Math.min(6, level + headingOffset)}` as "h2";
        return (
          <Tag id={anchors.get(node?.position?.start?.line ?? 0)} tabIndex={-1} className="scroll-mt-6 outline-none">
            {children}
          </Tag>
        );
      };
    return {
      h1: heading(1),
      h2: heading(2),
      h3: heading(3),
      // A fenced block in a known language is highlighted, as text (never HTML).
      code({ className, children }) {
        const language = /language-(\S+)/.exec(className ?? "")?.[1];
        if (!language) return <code className={className}>{children}</code>;
        return (
          <code className={className}>
            <HighlightedCode code={String(children).replace(/\n$/, "")} language={codeLanguage(language)} />
          </code>
        );
      },
      img: ({ alt }) => <span className="text-xs text-muted">[{alt || "image"}]</span>,
      a({ href, children }) {
        const to = href ? resolveGuideLink(href, page) : null;
        if (to) {
          return (
            <Link to={to} onClick={onNavigate} className="hover:decoration-ink">
              {children}
            </Link>
          );
        }
        return (
          <ExternalLink href={href} source="guide">
            {children}
          </ExternalLink>
        );
      },
    };
  }, [page, text, headingOffset, onNavigate]);
  return (
    <div className="prose-datalab">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
