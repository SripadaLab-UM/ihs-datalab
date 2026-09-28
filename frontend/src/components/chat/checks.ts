// What the checks on an answer say, each with its scope, in words a scientist
// would use. Three signals can look alike and disagree:
//   - DataLab's number check (sessions/tracing.py): every number in the answer
//     looked for in what the turn produced (query results, command output,
//     output files). Independent of the agent.
//   - The rigor review (sessions/rigor.py): the agent reviewing its own work
//     against the lab's checklist, whose first point is about untraced numbers.
//   - Steps that failed along the way: a query or command that didn't work,
//     which the agent may have done again.
// So each line says whose check it is, whether a problem remains, and, when two
// can disagree, why. Nothing here may claim more than the data shows: a
// problem counts as dealt with only on clear evidence, and the review's words
// are quoted unless they're a plain yes. Pure, so it's tested without a browser.
import type { Row, Step } from "./activity";
import type { Item, Turn } from "./transcript";

export interface CheckLine {
  key: "trace" | "review" | "differ" | "steps" | "notices";
  tone: "good" | "attn" | "bad" | "plain";
  text: string;
}

/**
 * A step that failed, and whether a later step of the same kind worked; or an
 * error DataLab reported during the turn (`notice`), which never counts as
 * dealt with: nothing shows whether it was.
 */
export interface Failure {
  title: string;
  resolved: boolean;
  notice?: boolean;
}

type Review = Extract<Item, { kind: "review" }>;

// Titles that name no particular thing (activity.ts): two such steps aren't
// the same step just because their titles match.
const GENERIC =
  /^(Ran a (command|query|short (R|Python) snippet)|Searched the files|Looked at the files in .*|Looked at the results of .*|Tried to install a package|Wrote \d+ files|Used .*)$/;

/**
 * The turn's failed steps and error notices, in order. A failed step is
 * resolved only when a later step of the same kind worked on at least
 * everything it touched: the same icon, every table or file the failed one
 * touched, and the same title unless that title is generic. A generic step
 * that touched nothing is never resolved. So a query on SLEEP and MOOD isn't
 * resolved by a later one on MOOD alone.
 */
export function failures(rows: Row[]): Failure[] {
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
  const out: Failure[] = [];
  flat.forEach((entry, index) => {
    if ("notice" in entry) {
      out.push({ title: entry.notice, resolved: false, notice: true });
      return;
    }
    const failed = entry.step;
    if (failed.tone !== "error") return;
    const touched = failed.touches ?? [];
    const generic = GENERIC.test(failed.title);
    const resolved =
      !(generic && touched.length === 0) &&
      flat.slice(index + 1).some((next) => {
        if (!("step" in next)) return false;
        const step = next.step;
        return (
          (step.tone === "done" || step.tone === "attn") &&
          step.icon === failed.icon &&
          (generic || step.title === failed.title) &&
          touched.every((t) => (step.touches ?? []).includes(t))
        );
      });
    out.push({ title: failed.title, resolved });
  });
  return out;
}

/** The review's point on traced claims as it wrote it, without the label or markup; "" if it has none. */
export function reviewTraceLine(text: string): string {
  // The whole point, not only its first line: an exception is often on the
  // next line ("Holds.\n  - Except the 12.4% mean…"). A point ends where the
  // next one starts (a number, a heading or a bold label) or at a blank line.
  const lines = text.split("\n");
  const start = lines.findIndex((l) => /traced claims/i.test(l));
  if (start < 0) return "";
  const point = [lines[start]];
  for (const l of lines.slice(start + 1)) {
    if (!l.trim() || /^\s*(\d+[.)]|#{1,6}\s|\*\*[A-Z])/.test(l)) break;
    point.push(l.replace(/^\s*[-*•]\s*/, ""));
  }
  return point
    .join(" ")
    .replace(/\*\*|__|`/g, "")
    .replace(/^.*?traced claims\s*[:\-–—]?\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * What the rigor review said on traced claims. "clean" only when the whole
 * point is a plain yes ("Holds.", "No untraced numbers.", "Yes: no untraced
 * numbers."); "flags" when it has any word of doubt or exception; "unknown"
 * otherwise. Anything but a plain yes is shown in the review's own words.
 */
export function reviewTraceVerdict(text: string): "clean" | "flags" | "unknown" {
  const line = reviewTraceLine(text).toLowerCase();
  if (!line) return "unknown";
  if (/^(holds|yes)\.?$|^no untraced numbers\.?$|^(holds|yes)\s*[.;:,—–-]\s*no untraced numbers\.?$/.test(line)) return "clean";
  if (/untraced|not|n['’]t|cannot|unclear|apart|other than|otherwise|except|but|however|although|though|\b(no|mostly|partly|partially)\b/.test(line)) return "flags";
  return "unknown";
}

/** The review's traced-claims point, short enough for a line: its first sentence, and one about tracing. */
function quoted(line: string): string {
  const sentences = line.match(/.+?(?:[.!?](?=\s|$)|$)/g)?.map((s) => s.trim()).filter(Boolean) ?? [line];
  const about = sentences.slice(1).find((s) => /trace/i.test(s));
  const text = about ? `${sentences[0]} … ${about}` : sentences.length > 1 ? `${sentences[0]} …` : sentences[0];
  return text.length > 240 ? `${text.slice(0, 239)}…` : text;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const names = (list: Failure[]) => list.slice(0, 2).map((f) => `“${f.title}”`).join(", ") + (list.length > 2 ? ", …" : "");

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
  const done = review?.status === "done" && !reviewing;
  const verdict = done && review ? reviewTraceVerdict(review.text) : "unknown";
  if (review) {
    const who = "Rigor review (the agent's check of its own work)";
    const said = done ? reviewTraceLine(review.text) : "";
    const text = reviewing
      ? `${who}: running…`
      : !done
        ? `${who}: didn't finish.`
        : verdict === "clean"
          ? `${who}: no untraced numbers.`
          : said
            ? `${who}, on traced numbers: “${quoted(said)}”`
            : `${who}: done; its notes are below.`;
    lines.push({ key: "review", tone: "plain", text });
  }
  if (trace && done && trace.untraced.length > 0) {
    lines.push({
      key: "differ",
      tone: "plain",
      text:
        "The two can differ: the rigor review is the agent checking its own work, while the number check is " +
        "DataLab's own comparison with what the turn actually produced. Go by DataLab's check.",
    });
  } else if (trace && done && trace.numbers > 0 && trace.untraced.length === 0 && verdict === "flags") {
    lines.push({
      key: "differ",
      tone: "plain",
      text:
        "The two can differ: DataLab's check only finds each number's value in what the turn produced; the review " +
        "also asks how it was worked out, so it can question a number whose value does appear.",
    });
  }
  const steps = failed.filter((f) => !f.notice);
  if (steps.length > 0) {
    const open = steps.filter((f) => !f.resolved);
    const lead = `${plural(steps.length, "step")} failed along the way`;
    if (open.length === 0) {
      lines.push({
        key: "steps",
        tone: "plain",
        text: `${lead}; for ${steps.length === 1 ? "it" : "each"}, a later step of the same kind worked.`,
      });
    } else {
      const worked = steps.length - open.length;
      lines.push({
        key: "steps",
        tone: "bad",
        text:
          `${lead}: ` +
          (worked ? `for ${worked}, a later step of the same kind worked; ` : "") +
          `${open.length} unresolved (${names(open)}).`,
      });
    }
  }
  const notices = failed.filter((f) => f.notice);
  if (notices.length > 0) {
    lines.push({
      key: "notices",
      tone: "bad",
      text:
        `${plural(notices.length, "error")} reported during the turn (${names(notices)}); ` +
        `DataLab can't tell whether ${notices.length === 1 ? "it was" : "they were"} dealt with.`,
    });
  }
  return lines;
}

/** The short form for the folded "How this answer was made" row. */
export function failureChip(failed: Failure[]): { text: string; tone?: "bad" } | null {
  if (failed.length === 0) return null;
  const steps = failed.filter((f) => !f.notice);
  const notices = failed.length - steps.length;
  const open = steps.filter((f) => !f.resolved).length;
  const parts = [
    steps.length > 0 &&
      (open === 0
        ? `${plural(steps.length, "failed step")}, a later one of the same kind worked`
        : `${plural(steps.length, "failed step")}, ${open} unresolved`),
    notices > 0 && `${plural(notices, "error")} reported`,
  ].filter(Boolean) as string[];
  return { text: parts.join("; "), tone: open > 0 || notices > 0 ? "bad" : undefined };
}
