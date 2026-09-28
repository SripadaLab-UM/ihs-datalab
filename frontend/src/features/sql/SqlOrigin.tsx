import type { ReactNode } from "react";
import { Link } from "react-router";

import type { SqlProposal } from "@/api/sql";
import { shortTable } from "@/components/chat/activity";
import { Button, Icon } from "@/components/ui";

import { type Origin, requestText } from "./drafts";

/**
 * "How this SQL was created": where the query in the editor came from, from
 * DataLab's own record of the chat turn that proposed it (its question, what
 * the agent said it assumed and relied on, the tables it looked up and the
 * queries it ran), with links to that turn in the chat and in the Workspace.
 * Everything here is text; nothing the agent wrote is rendered as HTML.
 */
export function SqlOrigin({
  origin,
  editorSql,
  onShowTurn,
  onClose,
}: {
  origin: Origin;
  editorSql: string;
  onShowTurn: (proposal: SqlProposal) => void;
  onClose: () => void;
}) {
  const p = origin.proposal;
  const edited = editorSql !== origin.insertedSql;
  const tables = unique([...p.tables, ...p.tables_named]);
  const described = p.tables_described.filter((t) => !tables.includes(t));
  const knowledge = unique([...p.knowledge, ...p.kb_read]);
  return (
    <section
      aria-label="How this SQL was created"
      className="mb-3 max-h-[45vh] overflow-y-auto rounded-[4px] border border-line bg-surface px-4 py-3 font-sans text-[13px] text-ink"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="dl-label">How this SQL was created</p>
          <p className="mt-1 font-serif text-[16px] leading-snug">{p.title || "The agent's proposed query"}</p>
          <p className="mt-0.5 text-[12px] text-muted">
            Proposed by the agent in turn {p.turn} of the chat, {when(p.created_at)}. It hasn't been run.
            {edited && <span className="text-attn"> You've changed the SQL since.</span>}
          </p>
        </div>
        <Button variant="ghost" className="px-2" aria-label="Hide how this SQL was created" onClick={onClose}>
          <Icon name="close" size={14} />
        </Button>
      </div>
      <dl className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <Entry term="You asked" wide>
          <p className="whitespace-pre-wrap">{requestText(p)}</p>
        </Entry>
        <Entry term="Assumptions to check">
          <List items={p.assumptions} empty="None stated." />
        </Entry>
        <Entry term="Parameters">
          {p.binds.length ? (
            <ul className="flex flex-col gap-0.5">
              {p.binds.map((b) => (
                <li key={b.name} className="font-mono text-[12px]">
                  :{b.name} = {b.value === null ? "(empty)" : String(b.value)}{" "}
                  <span className="font-sans text-[11.5px] text-faint">{b.type}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">None.</p>
          )}
        </Entry>
        <Entry term="Tables it reads">
          <List items={tables.map(shortTable)} empty="None named." mono />
          {described.length > 0 && (
            <p className="mt-1 text-[12px] text-muted">
              Also looked up: <span className="font-mono">{described.map(shortTable).join(", ")}</span>
            </p>
          )}
        </Entry>
        <Entry term="Knowledge consulted">
          <List items={knowledge} empty="No knowledge-base pages." mono />
        </Entry>
        <Entry term="Queries the agent ran in this turn" wide>
          {p.queries.length ? (
            <ul className="flex flex-col gap-0.5">
              {p.queries.map((q) => (
                <li key={q.id} className="text-[12.5px]">
                  <span className="font-mono">{q.tables.map(shortTable).join(", ") || "no tables"}</span>{" "}
                  <span className="text-muted">
                    · {q.status === "succeeded" && q.row_count !== null
                      ? `${q.row_count.toLocaleString()} row${q.row_count === 1 ? "" : "s"}`
                      : q.status}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">None: it worked from the catalog alone.</p>
          )}
        </Entry>
      </dl>
      {p.warnings.length > 0 && (
        <p className="mt-2 text-[12px] text-attn">The SQL check noted: {p.warnings.join(" · ")}</p>
      )}
      <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px]">
        <button
          type="button"
          onClick={() => onShowTurn(p)}
          className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
        >
          Show this turn in the chat
        </button>
        <Link
          to={`/workspace/${encodeURIComponent(p.conversation_id)}`}
          className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
        >
          Open its activity and queries in the Workspace
        </Link>
      </p>
    </section>
  );
}

function Entry({ term, wide, children }: { term: string; wide?: boolean; children: ReactNode }) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="text-[11.5px] text-faint">{term}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function List({ items, empty, mono }: { items: string[]; empty: string; mono?: boolean }) {
  if (!items.length) return <p className="text-muted">{empty}</p>;
  return (
    <ul className="flex list-disc flex-col gap-0.5 pl-4">
      {items.map((item) => (
        <li key={item} className={mono ? "font-mono text-[12px]" : undefined}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function unique(items: string[]): string[] {
  return [...new Set(items.filter(Boolean))];
}

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "earlier" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}
