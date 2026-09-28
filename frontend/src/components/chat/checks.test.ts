import { describe, expect, it } from "vitest";

import type { Row, Step } from "./activity";
import { checkLines, failureChip, failures, reviewTraceVerdict } from "./checks";

let n = 0;
const step = (title: string, tone: Step["tone"], icon: Step["icon"] = "code", touches: string[] = []): Row => ({
  type: "step",
  step: { key: `s${n++}`, icon, title, tone, chips: [], detail: null, touches },
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

describe("failed steps", () => {
  it("are resolved when a later step with the same title worked on the same things", () => {
    const failed = failures(PILOT);
    expect(failed).toHaveLength(5);
    expect(failed.every((f) => f.resolved)).toBe(true);
    expect(failureChip(failed)).toEqual({ text: "5 failed steps, a later one of the same kind worked" });
  });

  it("aren't resolved by a step of another kind or on another thing", () => {
    expect(failures([step("Ran pilot.R in R", "error"), step("Looked at the files in the workspace", "done", "folder")])[0].resolved).toBe(false);
    // Different tables.
    expect(failures([step("Queried A", "error", "db", ["A"]), step("Queried B", "done", "db", ["B"])])[0].resolved).toBe(false);
    // A query on SLEEP and MOOD isn't redone by one on MOOD alone.
    const partial = [step("Queried SLEEP and MOOD", "error", "db", ["SLEEP", "MOOD"]), step("Queried MOOD", "done", "db", ["MOOD"])];
    expect(failures(partial)[0].resolved).toBe(false);
    // A shared file isn't enough: the title must match too.
    expect(failures([step("Read a.csv and b.csv", "error", "eye", ["a.csv", "b.csv"]), step("Read a.csv", "done", "eye", ["a.csv"])])[0].resolved).toBe(false);
    // Nor a later step that failed, or one still running.
    expect(failures([step("Ran x.R in R", "error"), step("Ran x.R in R", "error"), step("Ran x.R in R", "now")]).some((f) => f.resolved)).toBe(false);
    // An earlier success doesn't count.
    expect(failures([step("Ran x.R in R", "done"), step("Ran x.R in R", "error")])[0].resolved).toBe(false);
  });

  it("with a generic title are resolved only by a step that touched everything they did", () => {
    expect(failures([step("Ran a command", "error"), step("Ran a command", "done")])[0].resolved).toBe(false);
    expect(failures([step("Ran a query", "error", "db"), step("Ran a query", "done", "db")])[0].resolved).toBe(false);
    expect(failures([step("Ran a short R snippet", "error"), step("Ran a short R snippet", "done")])[0].resolved).toBe(false);
    expect(failures([step("Searched the files", "error", "search"), step("Searched the files", "done", "search")])[0].resolved).toBe(false);
    const snippet = [step("Ran a short Python snippet", "error", "code", ["a.csv", "b.csv"])];
    expect(failures([...snippet, step("Ran a short Python snippet", "done", "code", ["a.csv"])])[0].resolved).toBe(false);
    expect(failures([...snippet, step("Ran a command", "done", "code", ["b.csv", "a.csv"])])[0].resolved).toBe(true);
  });

  it("say which remain unresolved", () => {
    const failed = failures([step("Ran pilot.R in R", "error"), step("Ran a.R in R", "error"), step("Ran a.R in R", "done")]);
    expect(failureChip(failed)).toEqual({ text: "2 failed steps, 1 unresolved", tone: "bad" });
    expect(checkLines({ reviewing: false, failed })).toEqual([
      {
        key: "steps",
        tone: "bad",
        text: "2 steps failed along the way: for 1, a later step of the same kind worked; 1 unresolved (“Ran pilot.R in R”).",
      },
    ]);
  });
});

it("never counts an error notice as dealt with, even when the turn finished", () => {
  const rows: Row[] = [{ type: "notice", key: "n", tone: "error", text: "Lost the connection" }, step("Ran x.R in R", "done")];
  const failed = failures(rows);
  expect(failed).toEqual([{ title: "Lost the connection", resolved: false, notice: true }]);
  expect(failureChip(failed)).toEqual({ text: "1 error reported", tone: "bad" });
  expect(checkLines({ reviewing: false, failed })).toEqual([
    {
      key: "notices",
      tone: "bad",
      text: "1 error reported during the turn (“Lost the connection”); DataLab can't tell whether it was dealt with.",
    },
  ]);
});

describe("the review's verdict on traced claims", () => {
  const point = (text: string) => `1. **Traced claims:** ${text}\n\n2. **Plan:** Holds.`;

  it("is clean only for a plain yes", () => {
    for (const text of ["Holds.", "holds", "Yes.", "No untraced numbers.", "Holds: no untraced numbers.", "Yes — no untraced numbers", "Holds; no untraced numbers."]) {
      expect(reviewTraceVerdict(point(text)), text).toBe("clean");
    }
  });

  it("flags anything with doubt or an exception (from the review of b1-clarity)", () => {
    for (const text of [
      "Not all numbers trace to a query result; 12.4% doesn't.",
      "I can't confirm there are no untraced numbers.",
      "Holds, apart from the 3.2 SD.",
      "unclear whether all numbers come from outputs.",
      "12.4 is not in any output; otherwise no untraced numbers.",
      "Holds? No — 12.4 has no source.",
      // And more.
      "Holds, except the 0.42 correlation.",
      "Holds, but 312 is worked out in the text.",
      "Holds for everything other than the 95% CI.",
      "Cannot be checked: the outputs are missing.",
      "Doesn't hold: 0.31 comes from nowhere.",
      "Mostly holds.",
      "No.",
      // The pilot's own wording is a yes, but not a plain one: it's quoted, not summarised.
      "Holds for the numeric claims in the answer. The estimate/CI are in /work/outputs/m.csv. I do not see untraced numbers in the answer.",
    ]) {
      expect(reviewTraceVerdict(point(text)), text).toBe("flags");
    }
  });

  it("is unknown when it can't be read either way", () => {
    expect(reviewTraceVerdict(point("Holds? No — 12.4 has no source.").replace("Traced claims", "Plan"))).toBe("unknown");
    expect(reviewTraceVerdict(point("See point 6."))).toBe("unknown");
    expect(reviewTraceVerdict(point("Holds? Yes."))).toBe("unknown");
    expect(reviewTraceVerdict("The answer is fine.")).toBe("unknown");
  });
});

const review = (text: string) => ({ kind: "review" as const, text, status: "done" as const });

it("labels each check with its scope, DataLab's own first, and says the two can differ", () => {
  const lines = checkLines({
    trace: { numbers: 13, untraced: ["12"] },
    review: review(
      "1. **Traced claims:** Holds for the numeric claims in the answer. The estimate/CI are in " +
        "`/work/outputs/m.csv`. I do not see untraced numbers in the answer.\n\n2. **Plan:** Mostly holds.",
    ),
    reviewing: false,
    failed: failures(PILOT),
  });
  expect(lines.map((l) => l.key)).toEqual(["trace", "review", "differ", "steps"]);
  expect(lines[0]).toEqual({
    key: "trace",
    tone: "attn",
    text: "Numbers checked against this turn's query results and outputs: 12 of 13 matched (1 not found, listed below: check it).",
  });
  // Not a plain yes, so in its own words.
  expect(lines[1].text).toBe(
    "Rigor review (the agent's check of its own work), on traced numbers: " +
      "“Holds for the numeric claims in the answer. … I do not see untraced numbers in the answer.”",
  );
  expect(lines[2].text).toMatch(/^The two can differ: the rigor review is the agent checking its own work/);
  expect(lines[2].text).toMatch(/Go by DataLab's check\.$/);
  expect(lines[3]).toEqual({
    key: "steps",
    tone: "plain",
    text: "5 steps failed along the way; for each, a later step of the same kind worked.",
  });
});

it("says 'no untraced numbers' only for a plain yes", () => {
  const plain = checkLines({ trace: { numbers: 4, untraced: [] }, review: review("1. Traced claims: Holds."), reviewing: false, failed: [] });
  expect(plain.map((l) => l.text)).toEqual([
    "Numbers checked against this turn's query results and outputs: all 4 matched.",
    "Rigor review (the agent's check of its own work): no untraced numbers.",
  ]);
  const doubt = checkLines({
    trace: { numbers: 4, untraced: [] },
    review: review("1. Traced claims: 12.4 is not in any output; otherwise no untraced numbers."),
    reviewing: false,
    failed: [],
  });
  expect(doubt.map((l) => l.key)).toEqual(["trace", "review", "differ"]);
  expect(doubt[1].text).toBe(
    "Rigor review (the agent's check of its own work), on traced numbers: “12.4 is not in any output; otherwise no untraced numbers.”",
  );
  expect(doubt.some((l) => /: no untraced numbers\.$/.test(l.text))).toBe(false);
  expect(doubt[2].text).toMatch(/also asks how it was worked out/);
});

it("waits for a running review, and says when one didn't finish", () => {
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
  const failed = checkLines({ review: { kind: "review", text: "", status: "failed" }, reviewing: false, failed: [] });
  expect(failed[0].text).toBe("Rigor review (the agent's check of its own work): didn't finish.");
});
