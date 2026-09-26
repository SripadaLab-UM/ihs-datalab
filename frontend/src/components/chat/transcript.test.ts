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
