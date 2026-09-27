import { describe, expect, it } from "vitest";

import { activityRows, checkChips, proposalState, proposalTitle, saveBlocker } from "./activity";
import { buildTranscript, type ConversationEvent, type KbProposalItem } from "./transcript";

let seq = 0;
const event = (type: string, data: Record<string, unknown> = {}): ConversationEvent => ({ seq: ++seq, type, data });

// As knowledge/service.py sends it.
const proposed = event("kb_proposal", {
  id: "kp_1",
  base: "abc",
  turn: 1,
  checkpoint: 2,
  files: [
    { path: "qc/wear-time.md", change: "added", added: 17, removed: 0, flags: [] },
    { path: "sources/fitbit.md", change: "modified", added: 2, removed: 2, flags: ["status: reviewed → draft"] },
  ],
  diff: "…",
  truncated: false,
  refused: [{ path: "notes/scratch.md", reason: "it isn't part of the knowledge base's layout (see AGENTS.md)" }],
  check: { errors: 0, data: 1, warnings: 2 },
});

describe("the transcript", () => {
  it("puts a proposal in the turn it follows, and keeps it up to date", () => {
    const turns = buildTranscript([
      event("user_message", { text: "Write up the wear-time rule." }),
      event("answer", { id: "a", text: "Done.", phase: "final_answer" }),
      event("turn_finished", { status: "completed" }),
      proposed,
      event("turn_done"),
    ]);
    const [item] = turns[0].items.filter((i) => i.kind === "kb_proposal") as KbProposalItem[];
    expect(item).toMatchObject({
      id: "kp_1",
      status: "open",
      files: [{ path: "qc/wear-time.md", added: 17 }, { path: "sources/fitbit.md", flags: ["status: reviewed → draft"] }],
      refused: [{ path: "notes/scratch.md" }],
      check: { errors: 0, data: 1, warnings: 2 },
    });
    expect(turns[0].status).toBe("completed");

    const later = buildTranscript([
      event("user_message", { text: "Q" }),
      proposed,
      event("kb_proposal_updated", { id: "kp_1", status: "saved", message: "Saved and shared.", commit: "bc2ac1b0" }),
      event("kb_proposal_updated", { id: "kp_other", status: "rejected" }),
    ]);
    const saved = later[0].items.find((i) => i.kind === "kb_proposal") as KbProposalItem;
    expect([saved.status, saved.message, saved.commit]).toEqual(["saved", "Saved and shared.", "bc2ac1b0"]);
  });

  it("makes a proposal row, never folded with the steps", () => {
    const [turn] = buildTranscript([event("user_message", { text: "Q" }), proposed]);
    expect(activityRows(turn.items, false)).toEqual([{ type: "proposal", proposal: turn.items[0] }]);
  });

  it("reads a malformed event without breaking", () => {
    const [turn] = buildTranscript([event("kb_proposal", { id: "kp_2", files: "nope", check: null })]);
    expect(turn.items[0]).toMatchObject({ files: [], refused: [], check: { errors: 0, data: 0, warnings: 0 } });
  });
});

describe("proposalTitle", () => {
  it("counts pages, or files when not all are pages", () => {
    const file = (path: string) => ({ path, change: "added", added: 1, removed: 0, flags: [] });
    expect(proposalTitle({ files: [file("a.md"), file("b.md")], refused: [] })).toBe("Knowledge edits proposed (2 pages)");
    expect(proposalTitle({ files: [file("skills/x/check.R")], refused: [] })).toBe("Knowledge edits proposed (1 file)");
    expect(proposalTitle({ files: [], refused: [{ path: "x", reason: "y" }] })).toBe("Knowledge edits the agent couldn't propose");
  });
});

describe("proposalState", () => {
  it.each([
    ["open", "needs you", true],
    ["saving", "saving…", false],
    ["saved", "saved and shared", false],
    ["conflict", "someone else changed it", true],
    ["check_failed", "the check stopped it", true],
    ["failed", "didn't save", true],
    ["rejected", "discarded", false],
    ["superseded", "replaced", false],
    ["withdrawn", "withdrawn", false],
  ])("%s", (status, label, actionable) => {
    const state = proposalState(status);
    expect(state.label).toBe(label);
    expect(state.actionable).toBe(actionable);
    expect(state.text).not.toBe("");
  });

  it("says plainly that nothing was shared when it didn't save", () => {
    expect(proposalState("failed", "Saving failed (OSError).").text).toBe(
      "Saving failed (OSError). Nothing was shared; you can try again.",
    );
    expect(proposalState("conflict").text).toMatch(/Nothing was shared/);
    expect(proposalState("check_failed").text).toMatch(/nothing was shared/);
    expect(proposalState("rejected").text).toBe("Discarded. Nothing was shared.");
  });
});

describe("checkChips", () => {
  it("says it passes, or what it found", () => {
    expect(checkChips({ errors: 0, data: 0, warnings: 0 })).toEqual([{ text: "passes the check", tone: "good" }]);
    expect(checkChips({ errors: 2, data: 1, warnings: 3 })).toEqual([
      { text: "2 errors", tone: "bad" },
      { text: "1 possible participant-data hit", tone: "attn" },
      { text: "3 warnings" },
    ]);
  });
});

describe("saveBlocker", () => {
  const findings = [
    { id: "e1", severity: "error" },
    { id: "d1", severity: "data" },
    { id: "w1", severity: "warning" },
  ];
  const base = { findings: [], confirmed: new Set<string>(), signedIn: true, sharing: 1 };

  it("lets it save only when nothing blocks it", () => {
    expect(saveBlocker(base)).toBeNull();
    expect(saveBlocker({ ...base, findings: [{ id: "w1", severity: "warning" }] })).toBeNull();
  });

  it("needs someone signed in, and something to share", () => {
    expect(saveBlocker({ ...base, signedIn: false })).toMatch(/Sign in to GitHub/);
    expect(saveBlocker({ ...base, sharing: 0 })).toMatch(/nothing to share/);
  });

  it("is blocked by every error, and by each data hit until it's confirmed", () => {
    expect(saveBlocker({ ...base, findings })).toMatch(/Fix the error/);
    const dataOnly = findings.slice(1);
    expect(saveBlocker({ ...base, findings: dataOnly })).toMatch(/possible participant data/);
    expect(saveBlocker({ ...base, findings: dataOnly, confirmed: new Set(["d1"]) })).toBeNull();
  });

  it("waits for each conflict to be resolved", () => {
    expect(saveBlocker({ ...base, unresolved: 2 })).toMatch(/Resolve the 2 files/);
  });
});
