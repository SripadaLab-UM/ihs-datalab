import clsx from "clsx";
import { useEffect, useId, useRef, useState } from "react";

import type { NumberSource } from "./provenance";

/** What the chat knows to say about a source, and how to open it. */
export interface SourceLinks {
  /** A command's text, by its event id (never its output). */
  commandText: (id: string) => string | undefined;
  /** Open a query's entry in the Data accessed log. */
  openQuery?: (id: string) => void;
  /** Open an output file. */
  openFile?: (path: string) => void;
}

/**
 * A number in an answer, with where it appears when you ask. It says what
 * DataLab checked: that the number appears in a command's output, a query's
 * result, or an output data file, not that it was computed there. It shows no
 * query rows or command output, only what to open.
 */
export function NumberSources({ text, sources, links }: { text: string; sources: NumberSource[]; links: SourceLinks }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const traced = sources.length > 0;
  return (
    <span ref={box} className="relative inline">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={panel}
        title={traced ? "Where this number appears" : "This number doesn't appear in anything the turn produced"}
        className={clsx(
          "underline decoration-dotted underline-offset-[3px]",
          traced ? "decoration-faint hover:decoration-ink" : "decoration-attn text-attn",
        )}
      >
        {text}
      </button>
      {open && (
        <span
          id={panel}
          role="dialog"
          aria-label={`Where ${text} appears`}
          className="absolute top-full left-0 z-20 mt-1 block w-[min(24rem,80vw)] border border-line bg-surface p-3 font-sans text-[13px] leading-snug text-ink shadow-sm"
        >
          {traced ? (
            <>
              <span className="block text-muted">{text} appears in:</span>
              <span className="mt-1.5 flex flex-col gap-1.5">
                {sources.map((source, i) => (
                  <Place key={i} source={source} links={links} />
                ))}
              </span>
              <span className="mt-2 block text-[12px] text-muted">
                Matched by value (allowing for rounding): it appears there, which doesn't show it was computed
                there.
              </span>
            </>
          ) : (
            <span className="block">
              It doesn't appear in any command output, query result, or output data file from this turn. It may
              have been worked out in the answer itself, or be wrong: check it.
            </span>
          )}
        </span>
      )}
    </span>
  );
}

function Place({ source, links }: { source: NumberSource; links: SourceLinks }) {
  if (source.kind === "query") {
    return (
      <span className="block">
        The result of query <span className="font-mono text-[12px]">{source.ref}</span>
        {links.openQuery && (
          <>
            {" "}
            <LinkButton onClick={() => links.openQuery?.(source.ref)}>in Data accessed</LinkButton>
          </>
        )}
      </span>
    );
  }
  if (source.kind === "file") {
    return (
      <span className="block">
        The output file{" "}
        {links.openFile ? (
          <LinkButton onClick={() => links.openFile?.(source.ref)}>{source.ref}</LinkButton>
        ) : (
          <span className="font-mono text-[12px]">{source.ref}</span>
        )}
      </span>
    );
  }
  const command = links.commandText(source.ref);
  return (
    <span className="block">
      The output of the command{" "}
      <span className="block truncate bg-sunken px-1.5 py-0.5 font-mono text-[12px]" title={command}>
        {command ?? "(not shown)"}
      </span>
    </span>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button type="button" onClick={onClick} className="text-ink underline decoration-faint underline-offset-2 hover:decoration-ink">
      {children}
    </button>
  );
}
