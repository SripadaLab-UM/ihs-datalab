import { buildTranscript, finalAnswer, type ConversationEvent } from "./transcript";

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
    const [turn] = buildTranscript([
      e("user_message", { text: "run it" }),
      e("stop_requested"),
      e("turn_finished", { status: "interrupted" }),
    ]);
    expect(turn.status).toBe("interrupted");
    expect(turn.items).toContainEqual({ kind: "notice", tone: "info", text: "Stopping…" });
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
