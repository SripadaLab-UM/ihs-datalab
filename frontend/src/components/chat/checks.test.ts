import { expect, it } from "vitest";

import type { Row, Step } from "./activity";
import { checkLines, failureChip, failures, reviewTraceVerdict } from "./checks";

const step = (title: string, tone: Step["tone"], icon: Step["icon"] = "code", touches: string[] = []): Row => ({
  type: "step",
  step: { key: title + tone + Math.random(), icon, title, tone, chips: [], detail: null, touches },
});

// The pilot conversation: a query fixed and rerun, a script fixed and rerun,
// and a missing tool (`column`) replaced with `cat` on the same three files.
const PILOT: Row[] = [
  step("Queried VW_DAILY_MOOD (2025)", "error", "db", ["VW_DAILY_MOOD (2025)"]),
  step("Queried VW_DAILY_MOOD (2025)", "done", "db", ["VW_DAILY_MOOD (2025)"]),
  step("Ran pilot.R in R", "error"),
  step("Ran pilot.R in R", "done"),
  step("Read flow.csv", "error", "eye", ["flow.csv"]),
  step("Read checks.csv", "error", "eye", ["checks.csv"]),
  step("Read model.csv", "error", "eye", ["model.csv"]),
  {
    type: "group",
    key: "g",
    icon: "eye",
    title: "Looked at 3 files",
    chips: [],
    steps: [
      { key: "a", icon: "eye", title: "Read flow.csv", tone: "done", chips: [], detail: null, touches: ["flow.csv"] },
      { key: "b", icon: "eye", title: "Read checks.csv", tone: "done", chips: [], detail: null, touches: ["checks.csv"] },
      { key: "c", icon: "eye", title: "Read model.csv", tone: "done", chips: [], detail: null, touches: ["model.csv"] },
    ],
  },
];

it("counts a failed step as resolved when a later step of the same kind worked", () => {
  const failed = failures(PILOT, "completed");
  expect(failed).toHaveLength(5);
  expect(failed.every((f) => f.resolved)).toBe(true);
  expect(failureChip(failed)).toEqual({ text: "5 failed steps, all retried successfully" });
});

it("leaves a failure unresolved when nothing like it worked afterwards", () => {
  const rows = [step("Ran pilot.R in R", "error"), step("Looked at the files", "done", "folder")];
  const failed = failures(rows, "completed");
  expect(failed).toEqual([{ title: "Ran pilot.R in R", resolved: false }]);
  expect(failureChip(failed)).toEqual({ text: "1 failed step, 1 unresolved", tone: "bad" });
  // A different query isn't a retry of the failed one.
  const queries = [step("Queried A", "error", "db", ["A"]), step("Queried B", "done", "db", ["B"])];
  expect(failures(queries, "completed")[0].resolved).toBe(false);
});

it("resolves an error notice only when the turn went on to finish", () => {
  const rows: Row[] = [{ type: "notice", key: "n", tone: "error", text: "Lost the connection" }, step("Ran x", "done")];
  expect(failures(rows, "completed")[0].resolved).toBe(true);
  expect(failures(rows, "failed")[0].resolved).toBe(false);
});

it("reads the review's verdict on traced claims", () => {
  const pilot =
    "1. **Traced claims:** Holds for the numeric claims in the answer. The estimate is in `/work/outputs/m.csv`. " +
    "I do not see untraced numbers in the answer.\n\n2. **Plan:** Mostly holds.";
  expect(reviewTraceVerdict(pilot)).toBe("clean");
  expect(reviewTraceVerdict("1. Traced claims: Partly holds. 0.42 isn't in any output.")).toBe("flags");
  expect(reviewTraceVerdict("1. **Traced claims:** Does not hold: 312 comes from nowhere.")).toBe("flags");
  expect(reviewTraceVerdict("1. Traced claims: Holds, but the 12 in the text is a setting.")).toBe("flags");
  expect(reviewTraceVerdict("The answer is fine.")).toBe("unknown");
});

const review = (text: string) => ({ kind: "review" as const, text, status: "done" as const });

it("labels each check with its scope, DataLab's own first, and says why they differ", () => {
  const lines = checkLines({
    trace: { numbers: 13, untraced: ["12"] },
    review: review("1. **Traced claims:** Holds. I do not see untraced numbers."),
    reviewing: false,
    failed: failures(PILOT, "completed"),
  });
  expect(lines.map((l) => l.key)).toEqual(["trace", "review", "differ", "steps"]);
  expect(lines[0]).toEqual({
    key: "trace",
    tone: "attn",
    text: "Numbers checked against this turn's query results and outputs: 12 of 13 matched (1 not found, listed below: check it).",
  });
  expect(lines[1].text).toBe("Rigor review (the agent's check of its own work): no untraced numbers.");
  expect(lines[2].text).toMatch(/^Why these differ: the rigor review is the agent checking its own work/);
  expect(lines[2].text).toMatch(/Go by DataLab's check\.$/);
  expect(lines[3]).toEqual({ key: "steps", tone: "plain", text: "5 steps failed along the way; all were retried successfully (resolved)." });
});

it("says when all numbers matched, and names what's unresolved", () => {
  const lines = checkLines({
    trace: { numbers: 4, untraced: [] },
    reviewing: false,
    failed: [
      { title: "Ran a.R in R", resolved: true },
      { title: "Queried B", resolved: false },
    ],
  });
  expect(lines[0]).toMatchObject({ tone: "good", text: "Numbers checked against this turn's query results and outputs: all 4 matched." });
  expect(lines[1]).toEqual({
    key: "steps",
    tone: "bad",
    text: "2 steps failed along the way: 1 retried successfully, 1 unresolved, not retried successfully (“Queried B”).",
  });
});

it("explains the other disagreement too, and waits for a running review", () => {
  const flags = checkLines({
    trace: { numbers: 3, untraced: [] },
    review: review("1. Traced claims: Partly holds."),
    reviewing: false,
    failed: [],
  });
  expect(flags.map((l) => l.key)).toEqual(["trace", "review", "differ"]);
  expect(flags[1].tone).toBe("attn");
  expect(flags[2].text).toMatch(/also asks how it was worked out/);
  const running = checkLines({
    trace: { numbers: 3, untraced: ["7"] },
    review: { kind: "review", text: "", status: "running" },
    reviewing: true,
    failed: [],
  });
  expect(running.map((l) => [l.key, l.text])).toEqual([
    ["trace", "Numbers checked against this turn's query results and outputs: 2 of 3 matched (1 not found, listed below: check it)."],
    ["review", "Rigor review (the agent's check of its own work): running…"],
  ]);
});
