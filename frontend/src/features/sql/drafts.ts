// The agent's proposed queries (propose_sql, backend data/sql_drafts.py) and the
// editor's draft: when a proposal may fill the editor, and when it only offers itself.
import { useCallback, useState } from "react";

import type { SqlProposal } from "@/api/sql";

/** The bind values as the editor holds them: text, as typed. */
export type DraftBinds = Record<string, string>;

/** The proposal in the editor, and the SQL and bind values as they were put there (to tell whether they've been edited since). */
export interface Origin {
  proposal: SqlProposal;
  insertedSql: string;
  /** Missing in an origin kept from before bind values were compared: counted as edited. */
  insertedBinds?: DraftBinds;
}

/** What the editor holds: its SQL and the bind values typed for it. */
export interface EditorDraft {
  sql: string;
  binds: DraftBinds;
}

/** A draft kept aside by "Use this query", to go back to. */
export interface KeptDraft {
  sql: string;
  binds: DraftBinds;
  origin: Origin | null;
}

/** The latest proposal event this tab has acted on, for the chat it came from. */
export interface Handled {
  chat: string;
  seq: number;
}

/**
 * What a new proposal does to the editor. It fills an empty editor, and one
 * that still holds the agent's last proposal untouched, SQL and bind values
 * both (a follow-up's revised draft). Anything else is the person's work: the
 * proposal is only offered. `snapshot` is the editor as it was when the
 * message was sent: if it has changed since (edits made while the agent
 * worked), it's never replaced.
 */
export function onArrival(editor: EditorDraft, origin: Origin | null, snapshot: EditorDraft | null): "fill" | "offer" {
  if (!editor.sql.trim()) return "fill";
  if (snapshot !== null && !sameDraft(editor, snapshot)) return "offer";
  if (origin?.insertedBinds && sameDraft(editor, { sql: origin.insertedSql, binds: origin.insertedBinds })) return "fill";
  return "offer";
}

/** The same SQL and the same bind values (a value left empty counts as not given). */
export function sameDraft(a: EditorDraft, b: EditorDraft): boolean {
  if (a.sql !== b.sql) return false;
  const names = new Set([...Object.keys(a.binds), ...Object.keys(b.binds)]);
  return [...names].every((name) => (a.binds[name] ?? "") === (b.binds[name] ?? ""));
}

/** The bind values the SQL uses, as lines the agent reads with the editor's SQL. */
export function bindLines(names: string[], binds: DraftBinds): string {
  if (!names.length) return "";
  const lines = names.map((name) => `-- :${name} = ${(binds[name] ?? "").replace(/[\r\n]+/g, " ") || "(empty)"}`);
  return ["", "-- Bind values in the editor:", ...lines].join("\n");
}

/**
 * The proposal to act on, if any, and how far the list has now been read.
 * Only a turn that finished well offers its proposal: a stopped or failed
 * turn's is passed over (it never touches the editor). A running turn's
 * waits. The latest proposal wins.
 */
export function nextProposal(proposals: SqlProposal[], handledSeq: number): { proposal: SqlProposal | null; seq: number } {
  const finished = proposals.filter((p) => p.turn_status !== "running");
  const seq = Math.max(handledSeq, ...finished.map((p) => p.seq));
  const fresh = finished.filter((p) => p.seq > handledSeq && p.turn_status === "completed");
  return { proposal: fresh.at(-1) ?? null, seq };
}

/** The proposal's bind values as the editor holds them. */
export function bindsOf(proposal: SqlProposal): DraftBinds {
  return Object.fromEntries(proposal.binds.map((b) => [b.name, b.value === null ? "" : String(b.value)]));
}

/** The person's question, as it reads beside the editor. */
export function requestText(proposal: SqlProposal): string {
  return proposal.request.trim() || "(no question text)";
}

/** A JSON value kept for this browser tab (sessionStorage), so a reload doesn't lose it. */
export function useTabJson<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = sessionStorage.getItem(key);
      return saved === null ? initial : (JSON.parse(saved) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        if (next === null || next === undefined) sessionStorage.removeItem(key);
        else sessionStorage.setItem(key, JSON.stringify(next));
      } catch {
        // Storage can be unavailable (a private window): the value lasts until the page goes.
      }
    },
    [key],
  );
  return [value, set];
}
