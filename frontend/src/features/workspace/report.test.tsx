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
