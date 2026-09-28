// What the agent did, in words a scientist would use (the Narrator design,
// docs/DESIGN.md). Turns a turn's items into rows: the agent's own
// commentary, and steps with an icon, a plain sentence, chips for what they
// touched, and a detail view. Pure, so it's tested without a browser.
//
// Everything here comes from the agent's container, so it's untrusted: it's
// rendered as text (and Markdown, for guides), never as HTML.

import type { Approval } from "./ApprovalCard";
import { finalAnswer, type Item, type KbProposalItem, type Turn } from "./transcript";

export type IconName =
  | "book" | "search" | "table" | "link" | "db" | "code" | "chart" | "shield"
  | "file" | "folder" | "eye" | "globe" | "pen" | "alert" | "check" | "spark";  // prettier-ignore

export interface Chip {
  text: string;
  tone?: "good" | "attn" | "bad";
}

export type Detail =
  | { kind: "guide"; name: string; text: string }
  | { kind: "tables"; searched: string; tables: { table: string; comment: string; columns: string[] }[] }
  | { kind: "columns"; table: string; comment: string; count: number; columns: { name: string; type: string; comment: string }[]; alsoIn: string[] }
  | { kind: "join"; tables: string[]; shared: { column: string; role: string; note: string }[]; notes: string[] }
  | { kind: "query"; sql: string; rows: number | null; columns: string[]; file: string; warnings: string[]; error: string | null }
  | { kind: "command"; command: string; output: string; exitCode: number | null }
  | { kind: "files"; paths: string[] }; // prettier-ignore

export interface Step {
  key: string;
  icon: IconName;
  title: string;
  chips: Chip[];
  tone: "done" | "now" | "attn" | "error";
  detail: Detail | null;
  /** The tables or files it touched, by name, for a folded group's chips. */
  touches?: string[];
}

export type Row =
  | { type: "step"; step: Step }
  | { type: "group"; key: string; icon: IconName; title: string; chips: Chip[]; steps: Step[] }
  | { type: "say"; key: string; text: string }
  | { type: "approval"; approval: Approval }
  | { type: "review"; index: number }
  | { type: "proposal"; proposal: KbProposalItem }
  | { type: "notice"; key: string; tone: "error" | "info"; text: string };

// The lab's guides (agent skills), by the name the agent reads them under.
const GUIDES: Record<string, string> = {
  "sql-extraction": "SQL extraction",
  "statistical-review": "statistical review",
  "academic-figures": "academic figures",
  "reproducible-report": "reproducible reports",
  "research-helper": "the research helper",
};

export function guideName(slug: string): string {
  return GUIDES[slug] ?? slug.replace(/[-_]+/g, " ");
}

/** "IHS_2025.FITBITDAILYDATA" → "FITBITDAILYDATA (2025)". */
export function shortTable(name: string): string {
  const match = /^IHS_(\d{4})\.(.+)$/i.exec(name);
  return match ? `${match[2]} (${match[1]})` : name;
}

const str = (value: unknown) => (typeof value === "string" ? value : "");
const list = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const basename = (path: string) => path.split("/").filter(Boolean).at(-1) ?? path;

function more(items: string[], shown = 3): Chip[] {
  const chips = items.slice(0, shown).map((text) => ({ text }));
  if (items.length > shown) chips.push({ text: `+${items.length - shown} more` });
  return chips;
}

function toolStep(item: Extract<Item, { kind: "tool" }>, live: boolean): Step | null {
  const args = (item.arguments ?? {}) as Record<string, unknown>;
  const s = item.summary ?? {};
  const failed = item.status === "failed" || Boolean(item.error);
  const tone: Step["tone"] = failed ? "error" : live && item.status !== "completed" ? "now" : "done";
  const base = { key: `tool-${item.id}`, tone };
  const error = failed ? [{ text: "didn't work", tone: "bad" as const }] : [];
  switch (item.tool) {
    case "search_catalog":
    case "find_concept": {
      const searched = str(args.query) || str(args.concept);
      const tables = list<{ table: string; comment: string; columns: string[] }>(s.tables);
      return {
        ...base,
        touches: searched ? [`“${searched}”`] : [],
        icon: "search",
        title:
          item.tool === "find_concept" ? `Looked for where “${searched}” is recorded` : `Looked for tables about “${searched}”`,
        chips: item.summary ? [{ text: plural(tables.length, "table") + " found" }, ...more(tables.map((t) => shortTable(t.table)), 2)] : error,
        detail: item.summary ? { kind: "tables", searched, tables } : null,
      };
    }
    case "describe_table": {
      const table = str(s.table) || str(args.table);
      const count = typeof s.column_count === "number" ? s.column_count : 0;
      return {
        ...base,
        touches: [shortTable(table)],
        icon: "table",
        title: `Read what's in ${shortTable(table)}`,
        chips: item.summary ? [{ text: plural(count, "column") }, ...more(list<string>(s.also_in).map((c) => `also in ${c.replace("IHS_", "")}`), 1)] : error,
        detail: item.summary
          ? { kind: "columns", table, comment: str(s.comment), count, columns: list(s.columns), alsoIn: list(s.also_in) }
          : null,
      };
    }
    case "join_paths": {
      const first = str(args.first_table), second = str(args.second_table); // prettier-ignore
      const shared = list<{ column: string; role: string; note: string }>(s.shared);
      const notes = list<string>(s.notes);
      const person = shared.find((k) => k.role === "participant");
      return {
        ...base,
        tone: failed ? "error" : notes.length ? "attn" : base.tone,
        icon: "link",
        title: `Checked how ${shortTable(first)} links to ${shortTable(second)}`,
        chips: item.summary
          ? [
              person ? { text: `by ${person.column}`, tone: "good" } : { text: "no shared participant ID", tone: "attn" },
              ...(notes.length ? [{ text: plural(notes.length, "heads-up", "heads-ups"), tone: "attn" as const }] : []),
            ]
          : error,
        detail: item.summary ? { kind: "join", tables: [first, second], shared, notes } : null,
      };
    }
    case "query": {
      const tables = list<string>(s.tables).length ? list<string>(s.tables) : tablesIn(str(args.sql));
      const rows = typeof s.row_count === "number" ? s.row_count : null;
      return {
        ...base,
        touches: tables.map(shortTable),
        icon: "db",
        title: tables.length ? `Queried ${tables.map(shortTable).slice(0, 2).join(" and ")}` : "Ran a query",
        chips: failed
          ? [{ text: "the database refused it", tone: "bad" }]
          : rows === null
            ? []
            : [{ text: plural(rows, "row") }, { text: "read-only", tone: "good" }],
        detail: {
          kind: "query",
          sql: str(args.sql),
          rows,
          columns: list(s.columns),
          file: str(s.result_file),
          warnings: list(s.warnings),
          error: item.error,
        },
      };
    }
    case "propose_sql": {
      // The query offered to the SQL Playground's editor (never run by the agent).
      const title = str(args.title);
      return {
        ...base,
        icon: "db",
        title: title ? `Proposed a query for the editor: ${title}` : "Proposed a query for the editor",
        chips: failed ? [{ text: "the SQL check refused it", tone: "bad" }] : [{ text: "not run", tone: "good" }],
        detail: null,
      };
    }
    case "suggest_kb_update": {
      // Shown as its own card under the answer; this is the step that made it.
      const title = str(args.title);
      return {
        ...base,
        icon: "book",
        title: title ? `Suggested a Knowledge update: ${title}` : "Suggested a Knowledge update",
        chips: failed ? [{ text: "DataLab refused it", tone: "bad" }] : [{ text: "for you to review", tone: "good" }],
        detail: null,
      };
    }
    case "check_workflow": {
      // DataLab's workflow check on the agent's draft: a failing one is never folded away as done.
      const count = typeof s.problem_count === "number" ? s.problem_count : 0;
      const invalid = item.summary ? s.valid !== true : false;
      return {
        ...base,
        tone: failed ? "error" : invalid ? "attn" : base.tone,
        icon: "check",
        title: "Checked the workflow file",
        chips: failed
          ? error
          : !item.summary
            ? []
            : invalid
              ? [{ text: plural(count || 1, "problem"), tone: "attn" }]
              : [{ text: "passes", tone: "good" }],
        detail: null,
      };
    }
    case "ask_research_helper":
    case "propose_plan":
      return null; // shown by their approval cards
    default:
      return { ...base, icon: "spark", title: `Used ${item.tool.replace(/_/g, " ")}`, chips: error, detail: null };
  }
}

/** The IHS tables a query reads, from its SQL (for steps recorded before summaries). */
export function tablesIn(sql: string): string[] {
  return [...new Set([...sql.matchAll(/\b(?:FROM|JOIN)\s+(IHS_\d{4}\.[A-Z0-9_$#]+)/gi)].map((m) => m[1].toUpperCase()))];
}

const GUIDE = String.raw`/etc/codex/skills/[\w-]+/SKILL\.md`;
const GUIDE_READ = new RegExp(
  String.raw`^(?:cat|nl -ba|head(?: -n ?\d+)?|sed -n ['"]?\d+(?:,\d+)?p['"]?)((?: ${GUIDE})+)$`,
);

/** The command inside a `bash -lc '…'` wrapper, as the agent wrote it. */
export function unwrapShell(command: string): string {
  return /^(?:\/(?:usr\/)?bin\/)?(?:ba|z)?sh\s+-l?c\s+(['"])([\s\S]*)\1\s*$/.exec(command.trim())?.[2] ?? command;
}

/** A shell command, described by what it did. */
export function describeCommand(command: string): { icon: IconName; title: string; guide?: string; files: string[] } {
  const inner = unwrapShell(command);
  // Only a plain read of the guide files counts as reading a guide: a command
  // that merely mentions one (in an echo, or chained) is shown as a command.
  const guides = GUIDE_READ.test(inner.trim())
    ? [...new Set([...inner.matchAll(/\/etc\/codex\/skills\/([\w-]+)\/SKILL\.md/g)].map((m) => m[1]))]
    : [];
  const paths = [...new Set([...inner.matchAll(/(?:\/work|\/data|\/inputs)\/[\w./-]*[\w-]/g)].map((m) => m[0]))];
  const files = [...new Set(paths.map(basename))].filter((f) => f && !/^(work|data|inputs|outputs|oracle)$/.test(f));
  const results = new Set(paths.filter((p) => /\/data\/oracle\/q_[\w-]+\.csv$/.test(p))).size;
  if (guides.length) {
    return {
      icon: "book",
      title: `Read the lab's guide on ${guides.map(guideName).join(" and ")}`,
      guide: guides.length === 1 ? guides[0] : undefined,
      files: [],
    };
  }
  const script = /\b(python3?|Rscript)\s+(?!-)(\S+\.(?:py|R|r))\b/.exec(inner);
  if (script) return { icon: "code", title: `Ran ${basename(script[2])}${script[1] === "Rscript" ? " in R" : ""}`, files: [] };
  if (/\bpython3?\s+-\s*<</.test(inner) || /\bpython3?\s+-c\b/.test(inner)) return { icon: "code", title: "Ran a short Python snippet", files };
  if (/\bRscript\s+-e\b|\bR\s+-e\b/.test(inner)) return { icon: "code", title: "Ran a short R snippet", files };
  if (results && !/\b(grep|rg)\b/.test(inner)) {
    return { icon: "eye", title: `Looked at the results of ${plural(results, "query", "queries")}`, files: [] };
  }
  if (/\b(grep|rg)\b/.test(inner)) return { icon: "search", title: files.length ? `Searched ${files.slice(0, 2).join(" and ")}${files.length > 2 ? " and more" : ""}` : "Searched the files", files };
  if (/\b(pip|pip3|install\.packages|npm)\b/.test(inner)) return { icon: "alert", title: "Tried to install a package", files };
  if (/\b(cat|sed|nl|head|tail|wc|less|awk|column|csvlook)\b/.test(inner) && files.length) {
    return { icon: "eye", title: `Read ${files.slice(0, 2).join(" and ")}${files.length > 2 ? ` and ${files.length - 2} more` : ""}`, files };
  }
  if (/\b(ls|find|tree|du)\b/.test(inner)) {
    const where = paths.length ? paths.slice(0, 2).map((p) => p.replace(/^\/work\/?/, "workspace/").replace(/\/$/, "")).join(" and ") : "the workspace";
    return { icon: "folder", title: `Looked at the files in ${where}`, files: [] };
  }
  return { icon: "code", title: "Ran a command", files };
}

function commandStep(item: Extract<Item, { kind: "command" }>, live: boolean): Step {
  const what = describeCommand(item.command);
  const failed = item.exitCode !== null && item.exitCode !== 0;
  // Only the turn still going can have a step at work: a stopped turn's unfinished command isn't.
  const running = live && item.exitCode === null && item.status !== "completed";
  return {
    key: `cmd-${item.id}`,
    icon: what.icon,
    title: what.title,
    tone: failed ? "error" : running ? "now" : "done",
    touches: what.files,
    chips: [
      ...more(what.files, 2),
      ...(failed
        ? [{ text: item.exitCode === 127 ? "a tool it tried isn't installed" : `stopped with an error (${item.exitCode})`, tone: "bad" as const }]
        : []),
    ],
    detail: what.guide && !failed
      ? { kind: "guide", name: guideName(what.guide), text: item.output }
      : { kind: "command", command: item.command, output: item.output, exitCode: item.exitCode },
  };
}

// Runs of three or more finished steps of the same kind fold into one row.
const GROUPS: Partial<Record<IconName, (n: number) => string>> = {
  db: (n) => `Ran ${n} queries`,
  table: (n) => `Read ${n} tables' columns`,
  eye: (n) => `Looked at ${n} files and results`,
  search: (n) => `Searched ${n} times`,
  code: (n) => `Ran ${n} commands`,
  pen: (n) => `Wrote files ${n} times`,
};

function fold(rows: Row[]): Row[] {
  const out: Row[] = [];
  let run: Step[] = [];
  const flush = () => {
    const name = run.length ? GROUPS[run[0].icon] : undefined;
    if (run.length >= 3 && name) {
      const touched = [...new Set(run.flatMap((step) => step.touches ?? []))];
      out.push({ type: "group", key: `group-${run[0].key}`, icon: run[0].icon, title: name(run.length), chips: more(touched, 3), steps: run });
    } else {
      out.push(...run.map((step) => ({ type: "step" as const, step })));
    }
    run = [];
  };
  for (const row of rows) {
    const foldable = row.type === "step" && row.step.tone === "done" && GROUPS[row.step.icon];
    if (foldable && run.length && run[0].icon === row.step.icon) {
      run.push(row.step);
    } else {
      flush();
      if (foldable) run.push(row.step);
      else out.push(row);
    }
  }
  flush();
  return out;
}

/** A turn's rows, in order. The final answer isn't one: it's shown on its own. */
export function activityRows(items: Item[], running: boolean): Row[] {
  const rows: Row[] = [];
  items.forEach((item, index) => {
    switch (item.kind) {
      case "message":
        if (item.phase !== "final_answer" && item.text.trim()) rows.push({ type: "say", key: `say-${item.id}`, text: item.text.trim() });
        break;
      case "tool": {
        const step = toolStep(item, running);
        if (step) rows.push({ type: "step", step });
        break;
      }
      case "command":
        rows.push({ type: "step", step: commandStep(item, running) });
        break;
      case "files":
        rows.push({
          type: "step",
          step: {
            key: `files-${index}`,
            icon: "pen",
            title: item.paths.length === 1 ? `Wrote ${basename(item.paths[0])}` : `Wrote ${plural(item.paths.length, "file")}`,
            chips: more(item.paths.map(basename), 3),
            tone: "done",
            detail: { kind: "files", paths: item.paths },
          },
        });
        break;
      case "approval":
        rows.push({ type: "approval", approval: item });
        break;
      case "review":
        rows.push({ type: "review", index });
        break;
      case "kb_proposal":
        rows.push({ type: "proposal", proposal: item });
        break;
      case "notice":
        rows.push({ type: "notice", key: `notice-${index}`, tone: item.tone, text: item.text });
        break;
      case "reasoning":
        break;
    }
  });
  return fold(rows);
}

/**
 * The live line: the step that's running, else the model's own short heading
 * for what it's thinking about. Never a repeat of the narration above it, and
 * never more specific than the events say.
 */
export function nowLine(rows: Row[], reasoning: string): string {
  // A notice (an export, "Stopping…") isn't the agent's work: the step still running is.
  const last = rows.findLast((row) => row.type !== "notice");
  if (last?.type === "step" && last.step.tone === "now") return `${last.step.title}…`;
  const heading = reasoning
    .split("\n")
    .reverse()
    .map((line) => /^\s*\*\*(.+?)\*\*\s*$/.exec(line)?.[1])
    .find(Boolean);
  if (heading) return `${heading}…`;
  return rows.length ? "Thinking about the next step…" : "Getting started…";
}

/**
 * The turn's answer, only if it has one: a message marked as the final answer,
 * or (for a turn that completed) its last message. A stopped or failed turn's
 * last words are narration, already in its story, not an answer.
 */
export function answerOf(turn: Turn): string {
  const hasFinal = turn.items.some((item) => item.kind === "message" && item.phase === "final_answer");
  return hasFinal || turn.status === "completed" ? finalAnswer(turn) : "";
}

// Proposed knowledge edits (docs/KNOWLEDGE_BASE.md, "How edits happen") --------

/** "Knowledge edits proposed (2 pages)". */
export function proposalTitle(proposal: Pick<KbProposalItem, "files" | "refused">): string {
  const n = proposal.files.length;
  if (n === 0) return "Knowledge edits the agent couldn't propose";
  const pages = proposal.files.every((f) => f.path.endsWith(".md"));
  return `Knowledge edits proposed (${plural(n, pages ? "page" : "file")})`;
}

export interface ProposalStateView {
  /** The chip beside the title. */
  label: string;
  tone?: "good" | "attn" | "bad" | "you";
  /** One plain sentence: where it is and what happens next. */
  text: string;
  /** The person can still edit, save or discard it. */
  actionable: boolean;
}

/** Each state of a proposal (backend ProposalState), in plain words. */
export function proposalState(status: string, message = ""): ProposalStateView {
  switch (status) {
    case "open":
      return { label: "needs you", tone: "you", text: "Nothing is shared until you save it. You can edit it first.", actionable: true };
    case "saving":
      return { label: "saving…", text: "Checking it, then saving and sharing it on GitHub…", actionable: false };
    case "saved":
      return { label: "saved and shared", tone: "good", text: message || "Saved and shared on GitHub.", actionable: false };
    case "conflict":
      return {
        label: "someone else changed it",
        tone: "attn",
        text: "Someone else changed these pages on GitHub since. Nothing was shared: choose what each should say, then save again.",
        actionable: true,
      };
    case "check_failed":
      return {
        label: "the check stopped it",
        tone: "bad",
        text: "The check found problems, so nothing was shared. Fix them (or confirm the possible participant data), then save again.",
        actionable: true,
      };
    case "failed":
      return { label: "didn't save", tone: "bad", text: `${message || "Saving failed."} Nothing was shared; you can try again.`, actionable: true };
    case "rejected":
      return { label: "discarded", text: "Discarded. Nothing was shared.", actionable: false };
    case "superseded":
      return { label: "replaced", text: "Replaced by a newer proposal, further down.", actionable: false };
    case "withdrawn":
      return { label: "withdrawn", text: "The agent undid these edits.", actionable: false };
    default:
      return { label: status.replace(/_/g, " "), text: message, actionable: false };
  }
}

/** The check's counts, as chips: errors block, possible participant data needs you. */
export function checkChips(check: { errors: number; data: number; warnings: number }): Chip[] {
  if (!check.errors && !check.data && !check.warnings) return [{ text: "passes the check", tone: "good" }];
  return [
    ...(check.errors ? [{ text: plural(check.errors, "error"), tone: "bad" as const }] : []),
    ...(check.data ? [{ text: plural(check.data, "possible participant-data hit"), tone: "attn" as const }] : []),
    ...(check.warnings ? [{ text: plural(check.warnings, "warning") }] : []),
  ];
}

/**
 * Why Save & share can't be pressed yet, or null when it can. Every error
 * blocks; so does each possible participant-data hit the person hasn't
 * confirmed; and there must be something to share, by someone signed in.
 */
export function saveBlocker(options: {
  findings: { id: string; severity: string }[];
  confirmed: ReadonlySet<string>;
  signedIn: boolean;
  sharing: number;
  /** Files someone else changed on GitHub that the person hasn't resolved yet. */
  unresolved?: number;
  /** Files with an open editor whose text isn't kept yet: saving would leave it out. */
  unkept?: string[];
}): string | null {
  const errors = options.findings.filter((f) => f.severity === "error").length;
  const unconfirmed = options.findings.filter((f) => f.severity === "data" && !options.confirmed.has(f.id)).length;
  if (options.unkept?.length) {
    return `Keep or cancel your edit of ${options.unkept.join(" and ")} first: Save & share saves only what's kept.`;
  }
  if (!options.signedIn) return "Sign in to GitHub (in Settings) to save and share.";
  if (options.sharing === 0) return "Every file is left out: there's nothing to share.";
  if (options.unresolved) {
    return `Resolve ${options.unresolved === 1 ? "the file" : `the ${options.unresolved} files`} someone else changed on GitHub first: write what each should say, or leave it out.`;
  }
  if (errors) return `Fix ${errors === 1 ? "the error" : `the ${errors} errors`} the check found first: edit the page, or leave it out.`;
  if (unconfirmed) {
    return `Check ${unconfirmed === 1 ? "the possible participant data" : `the ${unconfirmed} possible participant-data hits`} first, and confirm each is a false positive (or edit it out).`;
  }
  return null;
}
