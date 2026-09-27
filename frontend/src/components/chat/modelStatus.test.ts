import { describe, expect, it } from "vitest";

import { buildTranscript, canContinue, type ConversationEvent } from "./transcript";

const events = (...list: [string, Record<string, unknown>][]): ConversationEvent[] =>
  list.map(([type, data], seq) => ({ seq, type, data }));

describe("model status from DataLab's relay", () => {
  it("shows a retry while it's scheduled, and clears it on recovery", () => {
    const retrying = buildTranscript(
      events(["user_message", { text: "q" }], ["model_status", { state: "retrying", kind: "busy", wait_seconds: 20, model: "gpt-5.5" }]),
    )[0];
    expect(retrying.model).toMatchObject({ state: "retrying", kind: "busy", waitSeconds: 20 });
    const recovered = buildTranscript(
      events(
        ["user_message", { text: "q" }],
        ["model_status", { state: "retrying", kind: "busy", wait_seconds: 20 }],
        ["model_status", { state: "recovered" }],
      ),
    )[0];
    expect(recovered.model?.state).toBe("recovered");
  });

  it("explains a failed turn in plain words, and offers Continue only when it can help", () => {
    const busy = buildTranscript(
      events(
        ["user_message", { text: "q" }],
        ["model_status", { state: "failed", kind: "busy", status: 429 }],
        ["turn_finished", { status: "failed", error: "stream error: 429 Too Many Requests" }],
      ),
    )[0];
    const notice = busy.items.find((i) => i.kind === "notice");
    expect(notice?.kind === "notice" && notice.text).toContain("still busy");
    expect(notice?.kind === "notice" && notice.text).not.toContain("429");
    expect(canContinue(busy)).toBe(true);

    const quota = buildTranscript(
      events(
        ["user_message", { text: "q" }],
        ["model_status", { state: "failed", kind: "quota", status: 429 }],
        ["turn_finished", { status: "failed", error: "insufficient_quota" }],
      ),
    )[0];
    expect(canContinue(quota)).toBe(false); // retrying won't help until the allowance resets

    const crashed = buildTranscript(events(["user_message", { text: "q" }], ["turn_finished", { status: "failed", error: "stopped" }]))[0];
    expect(canContinue(crashed)).toBe(true);
  });

  it("shows Codex's repeated raw error once, as the plain explanation (a real run's events)", () => {
    const turn = buildTranscript(
      events(
        ["user_message", { text: "How many tables are in the 2025 cohort?" }],
        ["turn_started", { turn_id: "t" }],
        ["model_status", { state: "retrying", attempt: 1, wait_seconds: 8, kind: "busy", status: 429 }],
        ["model_status", { state: "retrying", attempt: 2, wait_seconds: 8, kind: "busy", status: 429 }],
        ["model_status", { state: "failed", attempt: 3, kind: "busy", status: 429 }],
        ["error", { message: "exceeded retry limit, last status: 429 Too Many Requests, request id: req_fake4" }],
        ["turn_finished", { turn_id: "t", status: "failed" }],
        ["error", { message: "exceeded retry limit, last status: 429 Too Many Requests, request id: req_fake4" }],
      ),
    )[0];
    const notices = turn.items.filter((i) => i.kind === "notice");
    expect(notices).toHaveLength(1);
    expect(notices[0].kind === "notice" && notices[0].text).toContain("still busy");
    expect(canContinue(turn)).toBe(true);
  });

  it("keeps a review's model trouble out of the turn", () => {
    const turn = buildTranscript(
      events(
        ["user_message", { text: "q" }],
        ["turn_finished", { status: "completed" }],
        ["review_started", {}],
        ["model_status", { state: "failed", kind: "busy", status: 429 }],
        ["error", { message: "exceeded retry limit, last status: 429" }],
        ["turn_finished", { status: "failed" }],
        ["review_finished", { status: "failed" }],
      ),
    )[0];
    expect(turn.items.filter((i) => i.kind === "notice")).toEqual([]);
    expect(turn.model).toBeUndefined();
    expect(turn.items.find((i) => i.kind === "review")).toMatchObject({ status: "failed" });
  });

  it("clears a stale status once the agent works again", () => {
    const turn = buildTranscript(
      events(
        ["user_message", { text: "q" }],
        ["model_status", { state: "failed", kind: "busy" }],
        ["answer_delta", { id: "m", text: "Here" }],
        ["error", { message: "a tool failed" }],
      ),
    )[0];
    expect(turn.model).toBeUndefined();
    const notice = turn.items.find((i) => i.kind === "notice");
    expect(notice?.kind === "notice" && notice.text).toBe("a tool failed"); // not model trouble
  });

  it("marks a turn Continue started", () => {
    const [first, second] = buildTranscript(
      events(["user_message", { text: "q" }], ["turn_finished", { status: "failed" }], ["user_message", { text: "…", continues: true }]),
    );
    expect(first.continues).toBe(false);
    expect(second.continues).toBe(true);
  });

  it("ignores unknown states", () => {
    const turn = buildTranscript(events(["user_message", { text: "q" }], ["model_status", { state: "exploded" }]))[0];
    expect(turn.model).toBeUndefined();
  });
});
