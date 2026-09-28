// Code, shown highlighted and read-only: in answers, guides, the file viewer,
// the Code tab, and the agent's commands. The one place DataLab turns code
// into highlighted text (the editors draw their own: components/editor).
//
// The code shows as plain text at once, and highlighted when the parser has
// loaded (highlight.ts, its own chunk). Tokens are React text nodes: nothing
// in the code is ever read as HTML.
import clsx from "clsx";
import { Fragment, useEffect, useState } from "react";

import type { Token } from "./highlight";
import { type CodeLanguage, MAX_HIGHLIGHT_CHARS } from "./languages";

export type { CodeLanguage } from "./languages";

/** `code` as lines of tokens once highlighted; null until then, and for code
 *  too long to highlight (over MAX_HIGHLIGHT_CHARS) or in no known language. */
export function useHighlight(code: string, language: CodeLanguage): Token[][] | null {
  const [result, setResult] = useState<{ code: string; language: CodeLanguage; lines: Token[][] } | null>(null);
  const wanted = language !== "text" && code.length <= MAX_HIGHLIGHT_CHARS;
  useEffect(() => {
    if (!wanted) return;
    let cancelled = false;
    import("./highlight")
      .then(({ highlightLines }) => highlightLines(code, language))
      .then((lines) => {
        if (!cancelled) setResult({ code, language, lines });
      })
      .catch(() => {
        // Plain text is still the code: nothing is lost.
      });
    return () => {
      cancelled = true;
    };
  }, [code, language, wanted]);
  return wanted && result?.code === code && result.language === language ? result.lines : null;
}

/** One line's tokens. */
export function Line({ tokens }: { tokens: Token[] }) {
  return (
    <>
      {tokens.map((token, i) =>
        token.className ? (
          <span key={i} className={token.className}>
            {token.text}
          </span>
        ) : (
          <Fragment key={i}>{token.text}</Fragment>
        ),
      )}
    </>
  );
}

/**
 * Highlighted code to put inside a `<code>` of one's own, such as a Markdown
 * block's: just the text, marked.
 */
export function HighlightedCode({ code, language }: { code: string; language: CodeLanguage }) {
  const lines = useHighlight(code, language);
  return lines ? <HighlightedLines lines={lines} /> : <>{code}</>;
}

/** A block of code, highlighted, with line numbers if asked for. */
export function CodeBlock({
  code,
  language,
  lineNumbers = false,
  wrap = false,
  className,
  label,
}: {
  code: string;
  language: CodeLanguage;
  lineNumbers?: boolean;
  /** Long lines wrap (commands) rather than scroll (scripts). */
  wrap?: boolean;
  className?: string;
  /** What the code is, for screen readers: "analysis.R, as saved at turn 2". */
  label?: string;
}) {
  const text = code.replace(/\n$/, "");
  const lines = useHighlight(text, language);
  const tooLong = text.length > MAX_HIGHLIGHT_CHARS;
  const shown = lines ?? text.split("\n").map((line) => (line ? [{ text: line, className: "" }] : []));
  return (
    <div className={clsx("min-w-0", className)}>
      {tooLong && language !== "text" && (
        <p className="mb-1.5 font-sans text-[12px] text-muted">Shown without highlighting: it's too long to highlight quickly.</p>
      )}
      <pre
        aria-label={label}
        data-language={language}
        data-highlighted={lines ? "true" : "false"}
        className={clsx(
          "dl-code overflow-auto bg-sunken py-2.5 font-mono text-[12.5px] leading-[1.6] text-ink",
          wrap ? "break-words whitespace-pre-wrap" : "whitespace-pre",
          lineNumbers ? "pr-3" : "px-3",
        )}
      >
        <code>
          {lineNumbers ? (
            shown.map((tokens, i) => (
              <span key={i} className="flex">
                <span aria-hidden className="w-[3.25em] shrink-0 pr-3 text-right text-faint select-none">
                  {i + 1}
                </span>
                <span className={clsx("min-w-0 flex-1", wrap && "break-words whitespace-pre-wrap")}>
                  <Line tokens={tokens} />
                  {"\n"}
                </span>
              </span>
            ))
          ) : lines ? (
            <HighlightedLines lines={lines} />
          ) : (
            text
          )}
        </code>
      </pre>
    </div>
  );
}

function HighlightedLines({ lines }: { lines: Token[][] }) {
  return (
    <>
      {lines.map((tokens, i) => (
        <Fragment key={i}>
          {i > 0 && "\n"}
          <Line tokens={tokens} />
        </Fragment>
      ))}
    </>
  );
}
