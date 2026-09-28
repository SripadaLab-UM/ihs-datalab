import clsx from "clsx";
import type { ReactNode } from "react";

import type { KbEntry } from "@/api/knowledge";
import { Chip } from "@/components/ui";

import { findPage, statusTone } from "./pages";

const shown = (value: unknown): string =>
  typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value);

/** A page's front matter, as facts: status and who reviewed it, the cohorts, typed evidence, limitations, related pages. */
export function FrontMatter({ fields, entries, onOpen }: { fields: Record<string, unknown>; entries: KbEntry[]; onOpen: (path: string) => void }) {
  const status = typeof fields.status === "string" ? fields.status : null;
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : value === undefined || value === null ? [] : [value]);
  const known = new Set(["id", "name", "kind", "status", "summary", "description", "evidence", "limitations", "related", "cohorts", "reviewed_by", "reviewed_on"]);
  const others = Object.entries(fields).filter(([key]) => !known.has(key));
  return (
    <section className="flex flex-col gap-3 border-y border-line py-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="font-serif text-[26px] leading-tight">{shown(fields.id ?? fields.name ?? "")}</h2>
        {status && <Chip tone={statusTone(status)}>{status}</Chip>}
        {typeof fields.kind === "string" && <Chip>{fields.kind}</Chip>}
        {list(fields.cohorts).map((c) => (
          <Chip key={shown(c)}>{shown(c)}</Chip>
        ))}
      </div>
      {(fields.summary ?? fields.description) !== undefined && (
        <p className="font-serif text-[18px] leading-relaxed">{shown(fields.summary ?? fields.description)}</p>
      )}
      {status === "reviewed" && fields.reviewed_by !== undefined && (
        <p className="font-sans text-[12.5px] text-muted">
          Reviewed by @{shown(fields.reviewed_by)}
          {fields.reviewed_on !== undefined && ` on ${shown(fields.reviewed_on)}`}
        </p>
      )}
      {status === "draft" && <p className="font-sans text-[12.5px] text-attn">Draft: no one has reviewed this page yet.</p>}
      {status === "deprecated" && <p className="font-sans text-[12.5px] text-danger">Deprecated: kept for the record, not to rely on.</p>}
      <Facts title="Evidence">
        {list(fields.evidence).map((item, i) => {
          const [kind, value] = item && typeof item === "object" && !Array.isArray(item) ? (Object.entries(item)[0] ?? ["", ""]) : ["", item];
          return (
            <li key={i} className="flex flex-wrap items-baseline gap-2">
              {kind && <span className="dl-label w-16 shrink-0">{kind}</span>}
              <span className="font-mono text-[12.5px] break-all">{shown(value)}</span>
            </li>
          );
        })}
      </Facts>
      <Facts title="Limitations">
        {list(fields.limitations).map((item, i) => (
          <li key={i} className="font-sans text-[13.5px]">
            {shown(item)}
          </li>
        ))}
      </Facts>
      <Facts title="Related">
        {list(fields.related).map((item) => {
          const found = findPage(entries, shown(item));
          return (
            <li key={shown(item)} className="inline">
              {found ? (
                <button type="button" onClick={() => onOpen(found.path)} className="font-mono text-[12.5px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
                  {shown(item)}
                </button>
              ) : (
                <span className="font-mono text-[12.5px] text-muted">{shown(item)}</span>
              )}
            </li>
          );
        })}
      </Facts>
      {others.length > 0 && (
        <Facts title="Other fields">
          {others.map(([key, value]) => (
            <li key={key} className="font-mono text-[12.5px]">
              {key}: {shown(value)}
            </li>
          ))}
        </Facts>
      )}
    </section>
  );
}

function Facts({ title, children }: { title: string; children: ReactNode[] }) {
  if (children.length === 0) return null;
  return (
    <div>
      <h3 className="dl-label">{title}</h3>
      <ul className={clsx("mt-1 flex gap-1.5", title === "Related" ? "flex-wrap gap-x-3" : "flex-col")}>{children}</ul>
    </div>
  );
}
