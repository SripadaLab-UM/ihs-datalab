// What the checks on an answer say, each with its scope, in words a scientist
// would use. Three signals can look alike and disagree:
//   - DataLab's number check (sessions/tracing.py): every number in the answer
//     looked for in what the turn produced (query results, command output,
//     output files). Independent of the agent.
//   - The rigor review (sessions/rigor.py): the agent reviewing its own work
//     against the lab's checklist, whose first point is about untraced numbers.
//   - Steps that failed along the way: a query or command that didn't work,
//     which the agent may have retried.
// So each line says whose check it is, whether a problem remains, and, when two
// seem to disagree, why they can. Pure, so it's tested without a browser.
import type { Row, Step } from "./activity";
import type { Item, Turn } from "./transcript";

export interface CheckLine {
  key: "trace" | "review" | "differ" | "steps";
  tone: "good" | "attn" | "bad" | "plain";
  text: string;
}

/** A step or notice that went wrong, and whether a later step did the same thing successfully. */
export interface Failure {
  title: string;
  resolved: boolean;
}

type Review = Extract<Item, { kind: "review" }>;

/**
 * The turn's failed steps and error notices, in order, each resolved or not.
 * A failed step is resolved when a later step of the same kind worked: the
 * same kind of action (the same icon) on the same thing (the same title, or a
 * table or file they share), such as a query that was fixed and run again, or
 * a missing tool swapped for another on the same file. An error notice is
 * resolved when the agent went on to finish the turn with a working step.
 */
export function failures(rows: Row[], status: Turn["status"]): Failure[] {
  type Entry = { step: Step } | { notice: string };
  const flat = rows.flatMap((row): Entry[] =>
    row.type === "step"
      ? [{ step: row.step }]
      : row.type === "group"
        ? row.steps.map((step) => ({ step }))
        : row.type === "notice" && row.tone === "error"
          ? [{ notice: row.text }]
          : [],
  );
  const worked = (entry: Entry) => "step" in entry && (entry.step.tone === "done" || entry.step.tone === "attn");
  const out: Failure[] = [];
  flat.forEach((entry, index) => {
    const later = flat.slice(index + 1);
    if ("notice" in entry) {
      out.push({ title: entry.notice, resolved: status === "completed" && later.some(worked) });
      return;
    }
    const failed = entry.step;
    if (failed.tone !== "error") return;
    const resolved = later.some(
      (next) =>
        "step" in next &&
        worked(next) &&
        next.step.icon === failed.icon &&
        (next.step.title === failed.title || (failed.touches ?? []).some((t) => (next.step.touches ?? []).includes(t))),
    );
    out.push({ title: failed.title, resolved });
  });
  return out;
}

/**
 * What the rigor review said about its first point, traced claims: "clean"
 * when it says the numbers hold, "flags" when it says some don't, "unknown"
 * when it can't be read. The review's own words stay in the review box.
 */
export function reviewTraceVerdict(text: string): "clean" | "flags" | "unknown" {
  const line = text
    .split("\n")
    .find((l) => /traced claims/i.test(l))
    ?.replace(/\*\*|__|`/g, "")
    .replace(/^.*?traced claims\s*[:\-–—]?\s*/i, "")
    .toLowerCase();
  if (!line) return "unknown";
  if (/\b(partly|partially|mostly|largely|except|however|but)\b|\bdoes(?:n['’]t| not) hold|\bnot hold|\bfails?\b|\bno\b\.?$|^no\b/.test(line)) return "flags";
  if (/\bno untraced|\b(?:do|did) not see (?:any )?untraced|\bdon['’]t see (?:any )?untraced|^holds\b|^yes\b|\ball (?:the )?numbers?\b.*\b(?:trace|come from)/.test(line)) {
    return "clean";
  }
  return "unknown";
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The checks on one answer, DataLab's own first. */
export function checkLines({
  trace,
  review,
  reviewing,
  failed,
}: {
  trace?: Turn["trace"];
  review?: Review;
  /** The review is still running. */
  reviewing: boolean;
  failed: Failure[];
}): CheckLine[] {
  const lines: CheckLine[] = [];
  const scope = "Numbers checked against this turn's query results and outputs";
  if (trace) {
    const missing = trace.untraced.length;
    if (trace.numbers === 0) {
      lines.push({ key: "trace", tone: "plain", text: `${scope}: the answer states no numbers to check.` });
    } else if (missing === 0) {
      lines.push({
        key: "trace",
        tone: "good",
        text: `${scope}: ${trace.numbers === 1 ? "the 1 number" : `all ${trace.numbers}`} matched.`,
      });
    } else {
      lines.push({
        key: "trace",
        tone: "attn",
        text: `${scope}: ${trace.numbers - missing} of ${trace.numbers} matched (${missing} not found, listed below: check ${missing === 1 ? "it" : "them"}).`,
      });
    }
  }
  const verdict = review?.status === "done" ? reviewTraceVerdict(review.text) : "unknown";
  if (review) {
    const who = "Rigor review (the agent's check of its own work)";
    const text = reviewing
      ? `${who}: running…`
      : review.status === "done"
        ? verdict === "clean"
          ? `${who}: no untraced numbers.`
          : verdict === "flags"
            ? `${who}: questions some numbers; its notes are below.`
            : `${who}: done; its notes are below.`
        : `${who}: didn't finish.`;
    lines.push({ key: "review", tone: !reviewing && verdict === "flags" ? "attn" : "plain", text });
  }
  if (trace && !reviewing && trace.untraced.length > 0 && verdict === "clean") {
    lines.push({
      key: "differ",
      tone: "plain",
      text:
        "Why these differ: the rigor review is the agent checking its own work, while the number check is DataLab's " +
        "own comparison with what the turn actually produced. Go by DataLab's check.",
    });
  } else if (trace && !reviewing && trace.numbers > 0 && trace.untraced.length === 0 && verdict === "flags") {
    lines.push({
      key: "differ",
      tone: "plain",
      text:
        "Why these differ: DataLab's check only finds each number's value in what the turn produced; the review also " +
        "asks how it was worked out, so it can question a number whose value does appear.",
    });
  }
  if (failed.length > 0) {
    const open = failed.filter((f) => !f.resolved);
    const lead = `${plural(failed.length, "step")} failed along the way`;
    if (open.length === 0) {
      lines.push({
        key: "steps",
        tone: "plain",
        text: `${lead}; ${failed.length === 1 ? "it was" : "all were"} retried successfully (resolved).`,
      });
    } else {
      const names = open.slice(0, 2).map((f) => `“${f.title}”`).join(", ") + (open.length > 2 ? ", …" : "");
      const retried = failed.length - open.length;
      lines.push({
        key: "steps",
        tone: "bad",
        text:
          `${lead}: ` +
          (retried ? `${retried} retried successfully, ` : "") +
          `${open.length} unresolved, not retried successfully (${names}).`,
      });
    }
  }
  return lines;
}

/** The short form for the folded "How this answer was made" row. */
export function failureChip(failed: Failure[]): { text: string; tone?: "bad" } | null {
  if (failed.length === 0) return null;
  const open = failed.filter((f) => !f.resolved).length;
  if (open === 0) return { text: `${plural(failed.length, "failed step")}, all retried successfully` };
  return { text: `${plural(failed.length, "failed step")}, ${open} unresolved`, tone: "bad" };
}
