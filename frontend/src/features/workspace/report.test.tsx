import { expect, it } from "vitest";

import type { Conversation } from "@/api/client";

import { buildReport } from "./report";

const conversation: Conversation = {
  id: "c1",
  kind: "data",
  mode: "analysis",
  title: "Steps",
  model: "gpt-5.5",
  created_at: "",
  updated_at: "",
  rigor_review: true,
  busy: false,
};

it("renders questions, answers, charts as SVG, and the SQL, with links as plain text", async () => {
  const chart = JSON.stringify({
    data: { values: [{ m: "a", v: 1 }, { m: "b", v: 2 }] },
    mark: "bar",
    encoding: { x: { field: "m", type: "nominal" }, y: { field: "v", type: "quantitative" } },
  });
  const html = await buildReport(
    conversation,
    [
      { seq: 1, type: "user_message", data: { text: "How many <steps>?" } },
      { seq: 2, type: "answer_started", data: { id: "m", phase: "final_answer" } },
      {
        seq: 3,
        type: "answer",
        data: { id: "m", phase: "final_answer", text: `See [source](https://x.org/?d=1).\n\n\`\`\`vega-lite\n${chart}\n\`\`\`` },
      },
      { seq: 4, type: "turn_finished", data: { status: "completed" } },
    ],
    [{ id: "q", started_at: "2026-09-26T00:00:00Z", status: "succeeded", sql_text: "SELECT 1 FROM dual", tables: ["IHS_2025.X"], row_count: 1, elapsed_ms: 1, result_file: null, message: null }],
    { includeWork: false },
  );
  expect(html).toContain("Contains study data");
  expect(html).toContain("How many &lt;steps&gt;?");
  expect(html).toContain("<svg");
  expect(html).not.toContain("href=\"https://x.org");
  expect(html).toContain("SELECT 1 FROM dual");
});

it("shows the commands the agent ran but never their output, which can hold rows", async () => {
  const html = await buildReport(
    conversation,
    [
      { seq: 1, type: "user_message", data: { text: "Look" } },
      { seq: 2, type: "command_started", data: { id: "c", command: "head /data/oracle/q_1.csv" } },
      { seq: 3, type: "command_output", data: { id: "c", text: "PARTICIPANTIDENTIFIER,MOOD\nP-0001,7\n" } },
      { seq: 4, type: "command_finished", data: { id: "c", exit_code: 0, status: "completed", output: "P-0002,6" } },
      { seq: 5, type: "turn_finished", data: { status: "completed" } },
    ],
    [],
    { includeWork: true },
  );
  expect(html).toContain("head /data/oracle/q_1.csv");
  expect(html).not.toContain("P-0001");
  expect(html).not.toContain("P-0002");
});

it("exports the approved plan whole, with its type, labels, and the person's own sections", async () => {
  const plan = {
    schema_version: 2,
    analysis_type: "data_quality",
    analysis_type_label: "Data quality or coverage",
    rationale: "It asks how complete the data are.",
    sections: [
      { kind: "question_and_purpose", label: "Question and purpose", content: "How complete is Garmin coverage?" },
      { kind: "additional", label: "Devices", content: "Garmin\nonly." },
    ],
  };
  const html = await buildReport(
    conversation,
    [
      { seq: 1, type: "user_message", data: { text: "Coverage?" } },
      { seq: 2, type: "approval_requested", data: { id: "ap1", kind: "analysis_plan", plan } },
      { seq: 3, type: "approval_answered", data: { id: "ap1", approved: true } },
      { seq: 4, type: "plan_approved", data: { approval: "ap1", plan, approved_at: "2026-09-26T12:00:00Z", sha256: "abcdef012345" } },
      { seq: 5, type: "user_message", data: { text: "Older?" } },
      { seq: 6, type: "approval_requested", data: { id: "ap0", kind: "analysis_plan", plan: { question: "Sleep?", cohort: "IHS_2024" } } },
      { seq: 7, type: "approval_answered", data: { id: "ap0", approved: true } },
      { seq: 8, type: "plan_approved", data: { approval: "ap0", plan: { question: "Sleep?", cohort: "IHS_2024" }, approved_at: "2026-09-01T12:00:00Z", sha256: "0123456789ab" } },
    ],
    [],
    { includeWork: false },
  );
  expect(html).toContain("Data quality or coverage. It asks how complete the data are.");
  expect(html).toContain("<em>Devices</em>: <span class=\"pre\">Garmin\nonly.</span>");
  expect(html).toContain("<em>Cohort, time window, and exclusions</em>");
});

it("exports a revision with what it replaced and what changed", async () => {
  const first = {
    schema_version: 2,
    analysis_type: "describe",
    analysis_type_label: "Describe or compare",
    rationale: "",
    sections: [{ kind: "measures", label: "Measures and summaries", content: "Fitbit sleep." }],
  };
  const second = {
    ...first,
    revises: { plan_id: "pl_000000000001", sha256: "a".repeat(64) },
    revision_reason: "Garmin data became available.",
    sections: [{ kind: "measures", label: "Measures and summaries", content: "Fitbit and Garmin sleep." }],
  };
  const html = await buildReport(
    conversation,
    [
      { seq: 1, type: "user_message", data: { text: "Sleep?" } },
      { seq: 2, type: "approval_requested", data: { id: "ap1", kind: "analysis_plan", plan: first } },
      { seq: 3, type: "approval_answered", data: { id: "ap1", approved: true } },
      { seq: 4, type: "plan_approved", data: { approval: "ap1", plan_id: "pl_000000000001", plan: first, approved_at: "2026-09-26T12:00:00Z", sha256: "a".repeat(64) } },
      { seq: 5, type: "approval_requested", data: { id: "ap2", kind: "analysis_plan", plan: second, compare_to: { label: "The approved plan it revises", plan: first, approved_at: "2026-09-26T12:00:00Z" } } },
      { seq: 6, type: "approval_answered", data: { id: "ap2", approved: true } },
      { seq: 7, type: "plan_approved", data: { approval: "ap2", plan_id: "pl_000000000002", plan: second, approved_at: "2026-09-26T13:00:00Z", sha256: "b".repeat(64) } },
    ],
    [],
    { includeWork: false },
  );
  expect(html).toContain("Replaced by a revision frozen");
  expect(html).toContain("Approved revision of the analysis plan");
  expect(html).toContain("Why: Garmin data became available. Changed: Measures and summaries.");
  expect(html).toContain("Fitbit sleep."); // the earlier version, as it was approved
});
