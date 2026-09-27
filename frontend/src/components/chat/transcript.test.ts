import { answerOf } from "./activity";
import { buildTranscript, canContinue, finalAnswer, type ConversationEvent } from "./transcript";

let seq = 0;
const e = (type: string, data: Record<string, unknown> = {}): ConversationEvent => ({ seq: ++seq, type, data });

describe("buildTranscript", () => {
  it("groups a turn's events under the user's message", () => {
    const turns = buildTranscript([
      e("user_message", { text: "How many?" }),
      e("turn_started"),
      e("answer_started", { id: "m1", phase: "commentary" }),
      e("answer_delta", { id: "m1", text: "Checking " }),
      e("answer_delta", { id: "m1", text: "the catalog." }),
      e("tool_call", { id: "t1", tool: "search_catalog", server: "ihs-data", status: "completed" }),
      e("command_started", { id: "c1", command: "Rscript a.R" }),
      e("command_output", { id: "c1", text: "ok\n" }),
      e("command_finished", { id: "c1", exit_code: 0, status: "completed" }),
      e("answer_started", { id: "m2", phase: "final_answer" }),
      e("answer_delta", { id: "m2", text: "150 participants." }),
      e("answer", { id: "m2", phase: "final_answer", text: "150 participants." }),
      e("turn_finished", { status: "completed" }),
    ]);
    expect(turns).toHaveLength(1);
    const [turn] = turns;
    expect(turn.userText).toBe("How many?");
    expect(turn.status).toBe("completed");
    expect(turn.items.map((i) => i.kind)).toEqual(["message", "tool", "command", "message"]);
    expect(turn.items[0]).toMatchObject({ text: "Checking the catalog." });
    expect(turn.items[2]).toMatchObject({ output: "ok\n", exitCode: 0 });
    expect(finalAnswer(turn)).toBe("150 participants.");
  });

  it("keeps a turn running until it finishes", () => {
    const [turn] = buildTranscript([e("user_message", { text: "hi" }), e("answer_delta", { id: "m", text: "Hel" })]);
    expect(turn.status).toBe("running");
    expect(finalAnswer(turn)).toBe(""); // not final yet
  });

  it("records a stopped turn", () => {
    const stopping = buildTranscript([e("user_message", { text: "run it" }), e("stop_requested")])[0];
    expect(stopping.items).toContainEqual({ kind: "notice", tone: "info", text: "Stopping…" });
    const [turn] = buildTranscript([
      e("user_message", { text: "run it" }),
      e("stop_requested"),
      e("turn_finished", { status: "interrupted" }),
    ]);
    expect(turn.status).toBe("interrupted");
    // Once stopped, the turn says so itself: no "Stopping…" left behind.
    expect(turn.items).not.toContainEqual({ kind: "notice", tone: "info", text: "Stopping…" });
  });

  it("starts a new turn for each user message", () => {
    const turns = buildTranscript([
      e("user_message", { text: "one" }),
      e("turn_finished", { status: "completed" }),
      e("user_message", { text: "two" }),
    ]);
    expect(turns.map((t) => t.userText)).toEqual(["one", "two"]);
  });

  it("caps very long command output", () => {
    const [turn] = buildTranscript([
      e("command_started", { id: "c", command: "cat big" }),
      e("command_output", { id: "c", text: "x".repeat(50_000) }),
    ]);
    const command = turn.items[0];
    expect(command.kind === "command" && command.output.length).toBe(20_000);
  });
});

describe("notices", () => {
  it("shows a notice from the backend", () => {
    const [turn] = buildTranscript([e("user_message", { text: "hi" }), e("notice", { text: "starting fresh" })]);
    expect(turn.items).toContainEqual({ kind: "notice", tone: "info", text: "starting fresh" });
  });
});

describe("workspace events", () => {
  it("shows a restore on its own, between turns, and lists file edits", () => {
    const turns = buildTranscript([
      e("user_message", { text: "Edit it" }),
      e("files_changed", { paths: ["/work/a.R", "/work/b.R"] }),
      e("turn_finished", { status: "completed" }),
      e("checkpoint", { number: 1 }),
      e("files_restored", { label: "After turn 1" }),
      e("user_message", { text: "Next" }),
    ]);
    expect(turns.map((t) => t.userText)).toEqual(["Edit it", "", "Next"]);
    expect(turns[0].items).toEqual([{ kind: "files", paths: ["/work/a.R", "/work/b.R"] }]);
    expect(turns[1].items[0]).toMatchObject({ kind: "notice", text: expect.stringContaining("after turn 1") });
  });
});

describe("exports", () => {
  const exported = e("exported", { folder: "/Users/me/Exports/Sleep", files: 2 });
  const notice = { kind: "notice", tone: "info", text: "You exported 2 file(s) to /Users/me/Exports/Sleep." };

  it("keeps a turn whole when files are exported while it runs", () => {
    const turns = buildTranscript([
      e("user_message", { text: "Plot sleep" }),
      e("turn_started"),
      e("command_started", { id: "c1", command: "Rscript plot.R" }),
      exported,
      e("command_output", { id: "c1", text: "ok\n" }),
      e("command_finished", { id: "c1", exit_code: 0, status: "completed" }),
      e("files_changed", { paths: ["/work/outputs/sleep.png"] }),
      e("answer_started", { id: "m1", phase: "final_answer" }),
      e("answer", { id: "m1", phase: "final_answer", text: "Here is the plot." }),
      e("turn_finished", { status: "completed" }),
      e("turn_done", {}),
    ]);
    expect(turns).toHaveLength(1);
    const [turn] = turns;
    expect(turn.status).toBe("completed");
    expect(turn.items.map((i) => i.kind)).toEqual(["command", "notice", "files", "message"]);
    expect(turn.items[1]).toEqual(notice);
    // The notice isn't an answer: the answer-first view shows the agent's.
    expect(finalAnswer(turn)).toBe("Here is the plot.");
    expect(answerOf(turn)).toBe("Here is the plot.");
  });

  it("offers Continue on the turn itself when it fails after an export", () => {
    const turns = buildTranscript([
      e("user_message", { text: "Plot sleep" }),
      e("answer_started", { id: "m1", phase: "commentary" }),
      exported,
      e("answer_delta", { id: "m1", text: "Reading the data." }),
      e("model_status", { state: "failed", kind: "busy" }),
      e("turn_finished", { status: "failed", error: "exceeded retry limit" }),
      e("turn_done", {}),
    ]);
    expect(turns).toHaveLength(1);
    const [turn] = turns;
    expect(turn.status).toBe("failed");
    expect(canContinue(turn)).toBe(true);
    expect(answerOf(turn)).toBe(""); // its narration isn't an answer
  });

  it("keeps an export during the checkpoint or the review with the turn", () => {
    const turns = buildTranscript([
      e("user_message", { text: "q" }),
      e("answer", { id: "m1", phase: "final_answer", text: "The mean was 7.2." }),
      e("turn_finished", { status: "completed" }),
      exported,
      e("trace", { numbers: 1, untraced: [] }),
      e("review_started", {}),
      exported,
      e("review", { text: "1. Traced claims: yes." }),
      e("review_finished", { status: "completed" }),
      e("notice", { text: "DataLab couldn't save a checkpoint of the files after this turn." }),
      e("turn_done", {}),
    ]);
    expect(turns).toHaveLength(1);
    const [turn] = turns;
    expect(turn.trace).toEqual({ numbers: 1, untraced: [] });
    expect(turn.items.map((i) => i.kind)).toEqual(["message", "notice", "review", "notice", "notice"]);
    expect(turn.items.find((i) => i.kind === "review")).toMatchObject({ status: "done" });
    expect(answerOf(turn)).toBe("The mean was 7.2.");
  });

  it("shows an export between turns on its own, and a failed turn before it offers no Continue", () => {
    const turns = buildTranscript([
      e("user_message", { text: "q" }),
      e("turn_finished", { status: "failed", error: "stopped" }),
      e("turn_done", {}),
      exported,
      e("user_message", { text: "next" }),
    ]);
    expect(turns.map((t) => t.userText)).toEqual(["q", "", "next"]);
    expect(turns[1]).toEqual({ userText: "", items: [notice], status: "completed" });
    // Continue is offered on the last entry only: the export's, here.
    expect(canContinue(turns[1])).toBe(false);
  });
});

it("shows a restore and attaching inputs on their own after turn_done", () => {
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("turn_finished", { status: "completed" }),
    e("turn_done", {}),
    e("files_restored", { label: "After turn 1" }),
    e("input_attached", { items: [{ path: "/inputs/data.csv", kind: "file" }] }),
    e("input_removed", { items: [{ path: "/inputs/data.csv", kind: "file" }] }),
  ]);
  expect(turns.map((t) => [t.userText, t.items.length])).toEqual([["q", 0], ["", 1], ["", 1], ["", 1]]);
});

it("shows attaching and removing inputs between turns", () => {
  const turns = buildTranscript([
    e("user_message", { text: "hi" }),
    e("turn_finished", { status: "completed" }),
    e("input_attached", { items: [{ path: "/inputs/data.csv", kind: "file" }] }),
  ]);
  expect(turns[1].items[0]).toMatchObject({ kind: "notice", text: expect.stringContaining("/inputs/data.csv") });
});


it("tracks a research-helper approval from request to answer", () => {
  const turns = buildTranscript([
    e("user_message", { text: "look it up" }),
    e("approval_requested", { id: "ap1", question: "lme4 slopes?" }),
    e("approval_answered", { id: "ap1", approved: true, question: "Random slopes in lme4?" }),
    e("helper_answered", { approval: "ap1", status: "answered", answer: "Use (1 + x | id)." }),
    e("approval_requested", { id: "ap2", question: "again?" }),
    e("turn_finished", { status: "interrupted" }),
  ]);
  const approvals = turns[0].items.filter((i) => i.kind === "approval");
  expect(approvals).toMatchObject([
    { id: "ap1", state: "approved", sent: "Random slopes in lme4?", answer: "Use (1 + x | id)." },
    { id: "ap2", state: "withdrawn" },
  ]);
});

it("keeps the rigor review apart from the answer, and records the trace", () => {
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("answer_started", { id: "m1", phase: "final_answer" }),
    e("answer", { id: "m1", phase: "final_answer", text: "The mean was 7.2." }),
    e("turn_finished", { status: "completed" }),
    e("trace", { numbers: 1, untraced: [] }),
    e("review_started", {}),
    e("turn_started", {}),
    e("answer_started", { id: "m2", phase: "final_answer" }),
    e("answer", { id: "m2", phase: "final_answer", text: "review chatter" }),
    e("review", { text: "1. Traced claims: yes." }),
    e("turn_finished", { status: "completed" }),
    e("review_finished", { status: "completed" }),
  ]);
  expect(turns).toHaveLength(1);
  expect(finalAnswer(turns[0])).toBe("The mean was 7.2.");
  expect(turns[0].trace).toEqual({ numbers: 1, untraced: [] });
  expect(turns[0].items.find((i) => i.kind === "review")).toMatchObject({ text: "1. Traced claims: yes.", status: "done" });
});

it("shows an approved plan as frozen", () => {
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("approval_requested", { id: "ap1", kind: "analysis_plan", plan: { question: "Sleep?" } }),
    e("approval_answered", { id: "ap1", approved: true }),
    e("plan_approved", { approval: "ap1", plan: { question: "Sleep and mood?" }, approved_at: "2026-09-26T12:00:00Z", sha256: "abc" }),
  ]);
  expect(turns[0].items[0]).toMatchObject({
    approvalKind: "analysis_plan",
    state: "approved",
    plan: { question: "Sleep and mood?" },
    frozen: { sha256: "abc" },
  });
});

it("keeps a version-2 plan whole, its own sections included", () => {
  const plan = {
    schema_version: 2,
    analysis_type: "describe",
    analysis_type_label: "Describe or compare",
    rationale: "",
    sections: [
      { kind: "question_and_purpose", label: "Question and purpose", content: "Sleep?" },
      { kind: "additional", label: "Devices", content: "Fitbit only." },
    ],
  };
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("approval_requested", { id: "ap1", kind: "analysis_plan", plan }),
    e("approval_answered", { id: "ap1", approved: true }),
    e("plan_approved", { approval: "ap1", plan, approved_at: "2026-09-26T12:00:00Z", sha256: "abc" }),
  ]);
  expect(turns[0].items[0]).toMatchObject({ plan, frozen: { sha256: "abc" } });
});

it("marks a plan replaced by an approved revision, and keeps what the revision is compared with", () => {
  const first = { schema_version: 2, analysis_type: "describe", analysis_type_label: "Describe or compare", rationale: "", sections: [] };
  const second = { ...first, revises: { plan_id: "pl_1", sha256: "a".repeat(64) }, revision_reason: "Add Garmin." };
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("approval_requested", { id: "ap1", kind: "analysis_plan", plan: first }),
    e("approval_answered", { id: "ap1", approved: true }),
    e("plan_approved", { approval: "ap1", plan_id: "pl_1", plan: first, approved_at: "2026-09-26T12:00:00Z", sha256: "a".repeat(64) }),
    e("approval_requested", { id: "ap2", kind: "analysis_plan", plan: second, compare_to: { label: "The approved plan it revises", plan: first } }),
    e("approval_answered", { id: "ap2", approved: true }),
    e("plan_approved", { approval: "ap2", plan_id: "pl_2", plan: second, approved_at: "2026-09-26T13:00:00Z", sha256: "b".repeat(64) }),
    e("approval_requested", { id: "ap3", kind: "analysis_plan", plan: first }),
    e("approval_answered", { id: "ap3", approved: false, change_type: "prediction", change_type_label: "Prediction" }),
  ]);
  const [one, two, three] = turns[0].items;
  expect(one).toMatchObject({ planId: "pl_1", supersededBy: { planId: "pl_2", at: "2026-09-26T13:00:00Z" } });
  expect(two).toMatchObject({ planId: "pl_2", compareTo: { plan: first } });
  expect(two).not.toHaveProperty("supersededBy");
  expect(three).toMatchObject({ state: "declined", changeTypeLabel: "Prediction" });
});

it("a review that never finished doesn't swallow later turns", () => {
  const turns = buildTranscript([
    e("user_message", { text: "one" }),
    e("answer", { id: "m1", phase: "final_answer", text: "First." }),
    e("turn_finished", { status: "completed" }),
    e("review_started", {}),
    // DataLab stopped here, mid-review.
    e("user_message", { text: "two" }),
    e("answer_started", { id: "m2", phase: "final_answer" }),
    e("answer", { id: "m2", phase: "final_answer", text: "Second." }),
    e("turn_finished", { status: "completed" }),
  ]);
  expect(turns.map((t) => [t.status, finalAnswer(t)])).toEqual([
    ["completed", "First."],
    ["completed", "Second."],
  ]);
  expect(turns[0].items.find((i) => i.kind === "review")).toMatchObject({ status: "failed" });
});

it("turn_done ends the turn and any review still open", () => {
  const turns = buildTranscript([
    e("user_message", { text: "one" }),
    e("turn_started"),
    e("review_started", {}),
    e("turn_done", {}),
  ]);
  expect(turns[0].status).toBe("completed");
  expect(turns[0].items.find((i) => i.kind === "review")).toMatchObject({ status: "failed" });
});

it("carries the approved plan, and says a plan the turn ended before freezing wasn't frozen", () => {
  const proposed = { schema_version: 2, analysis_type: "describe", analysis_type_label: "Describe or compare", rationale: "", sections: [] };
  const approved = { ...proposed, rationale: "Edited." };
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("approval_requested", { id: "ap1", kind: "analysis_plan", plan: proposed }),
    e("approval_answered", { id: "ap1", approved: true, plan: approved }),
    e("turn_finished", { status: "interrupted" }),
  ]);
  expect(turns[0].items[0]).toMatchObject({ plan: approved, notFrozen: "The turn ended before the plan was frozen." });
  expect(turns[0].items[0]).not.toHaveProperty("answer");
});

it("keeps the reason a plan wasn't frozen when the turn then ends", () => {
  const plan = { schema_version: 2, analysis_type: "describe", analysis_type_label: "Describe or compare", rationale: "", sections: [] };
  const turns = buildTranscript([
    e("user_message", { text: "q" }),
    e("approval_requested", { id: "ap1", kind: "analysis_plan", plan }),
    e("approval_answered", { id: "ap1", approved: true, plan }),
    e("plan_not_frozen", { approval: "ap1", reason: "Another revision was approved first." }),
    e("turn_finished", { status: "completed" }),
  ]);
  expect(turns[0].items[0]).toMatchObject({ notFrozen: "Another revision was approved first." });
});
