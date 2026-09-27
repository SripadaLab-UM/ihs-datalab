import { describe, expect, it } from "vitest";

import { activityRows, answerOf, describeCommand, nowLine, shortTable, tablesIn } from "./activity";
import type { Item } from "./transcript";

// Commands as Codex really sends them (from practice sessions, synthetic data).
describe("describeCommand", () => {
  it.each([
    [`/bin/bash -lc "sed -n '1,220p' /etc/codex/skills/sql-extraction/SKILL.md"`, "Read the lab's guide on SQL extraction", "book"],
    [`/bin/bash -lc 'cat /etc/codex/skills/statistical-review/SKILL.md'`, "Read the lab's guide on statistical review", "book"],
    [`/bin/bash -lc 'python /work/analyze_fitbit_steps_2025.py'`, "Ran analyze_fitbit_steps_2025.py", "code"],
    [`/bin/bash -lc "python - <<'PY'\nimport pandas as pd\nm=pd.read_csv('/work/outputs/steps_monthly_source_data.csv')\nPY"`, "Ran a short Python snippet", "code"],
    [`/bin/bash -lc 'cat /data/oracle/q_20260926T125410_e706db.csv'`, "Looked at the results of 1 query", "eye"],
    [`/bin/bash -lc 'ls -lh /work/outputs'`, "Looked at the files in workspace/outputs", "folder"],
    [`/bin/bash -lc "grep -nE \\"SECONDARYIDENTIFIER|1315\\" /work/analyze_fitbit_steps_2025.py | head"`, "Searched analyze_fitbit_steps_2025.py", "search"],
    [`/bin/bash -lc "nl -ba /work/outputs/report.html | sed -n '239,312p'"`, "Read report.html", "eye"],
    [`/bin/bash -lc "Rscript /work/model.R"`, "Ran model.R in R", "code"],
    ["echo hello", "Ran a command", "code"],
  ])("%s", (command, title, icon) => {
    const described = describeCommand(command);
    expect(described.title).toBe(title);
    expect(described.icon).toBe(icon);
  });

  it("counts only a plain read of a guide as reading it", () => {
    expect(describeCommand(`bash -lc 'head -n 80 /etc/codex/skills/sql-extraction/SKILL.md'`).guide).toBe("sql-extraction");
    for (const spoof of [
      `/bin/bash -lc 'echo /etc/codex/skills/sql-extraction/SKILL.md; cat /data/oracle/q_1.csv'`,
      `/bin/bash -lc 'cat /etc/codex/skills/sql-extraction/SKILL.md && cat /work/secret.txt'`,
      `/bin/bash -lc 'python a.py # /etc/codex/skills/x/SKILL.md'`,
    ]) {
      expect(describeCommand(spoof).icon).not.toBe("book");
      expect(describeCommand(spoof).guide).toBeUndefined();
    }
  });

  it("knows which guide it read, so the chat can show it", () => {
    expect(describeCommand(`/bin/bash -lc 'cat /etc/codex/skills/academic-figures/SKILL.md'`).guide).toBe("academic-figures");
  });
});

describe("activityRows", () => {
  const tool = (tool: string, args: unknown, summary: Record<string, unknown> | null, status = "completed"): Item => ({
    kind: "tool", id: tool, tool, server: "ihs-data", status, arguments: args, error: null, summary,
  }); // prettier-ignore

  it("turns tools into plain sentences with what they touched", () => {
    const rows = activityRows(
      [
        { kind: "message", id: "m1", phase: "commentary", text: "I'll check the catalog first." },
        tool("search_catalog", { query: "fitbit steps" }, { tables: [{ table: "IHS_2025.FITBITDAILYDATA", comment: "", columns: [] }] }),
        tool("describe_table", { table: "IHS_2025.FITBITDAILYDATA" }, { table: "IHS_2025.FITBITDAILYDATA", column_count: 49, columns: [], also_in: ["IHS_2024"] }),
        tool("join_paths", { first_table: "IHS_2025.FITBITDAILYDATA", second_table: "IHS_2025.STUDYPARTICIPANTS" },
          { shared: [{ column: "PARTICIPANTIDENTIFIER", role: "participant", note: "" }], notes: ["no shared date"] }),
        tool("query", { sql: "SELECT 1 FROM IHS_2025.X" }, { row_count: 1, columns: ["N"], result_file: "/data/oracle/q.csv", tables: ["IHS_2025.X"], warnings: [] }),
        { kind: "files", paths: ["/work/outputs/steps.png"] },
        { kind: "message", id: "m2", phase: "final_answer", text: "The answer." },
      ],
      false,
    ); // prettier-ignore
    expect(rows.map((r) => (r.type === "step" ? r.step.title : r.type === "say" ? r.text : r.type))).toEqual([
      "I'll check the catalog first.",
      "Looked for tables about “fitbit steps”",
      "Read what's in FITBITDAILYDATA (2025)",
      "Checked how FITBITDAILYDATA (2025) links to STUDYPARTICIPANTS (2025)",
      "Queried X (2025)",
      "Wrote steps.png",
    ]);
    const join = rows[3];
    expect(join.type === "step" && join.step.tone).toBe("attn"); // it has a heads-up
    const query = rows[4];
    expect(query.type === "step" && query.step.chips.map((c) => c.text)).toEqual(["1 row", "read-only"]);
  });

  it("marks a failed query, and a running step as live", () => {
    const failed = activityRows([{ ...(tool("query", { sql: "DELETE" }, null, "failed") as object), error: "Only SELECT" } as Item], false)[0];
    expect(failed.type === "step" && failed.step.tone).toBe("error");
    const live = activityRows([{ kind: "command", id: "c", command: "python a.py", output: "", exitCode: null, status: "running" }], true);
    expect(live[0].type === "step" && live[0].step.tone).toBe("now");
    expect(nowLine(live, "")).toBe("Ran a.py…");
    // Exporting while it runs doesn't hide the step that's running.
    const exported = activityRows(
      [
        { kind: "command", id: "c", command: "python a.py", output: "", exitCode: null, status: "running" },
        { kind: "notice", tone: "info", text: "You exported 1 file(s) to /Users/me/Exports." },
      ],
      true,
    );
    expect(nowLine(exported, "")).toBe("Ran a.py…");
    // From the design review: the live line repeated the narration above it.
    const talking = activityRows([{ kind: "message", id: "m", phase: "commentary", text: "I'll check the catalog first." }], true);
    expect(nowLine(talking, "")).toBe("Thinking about the next step…");
    expect(nowLine(talking, "**Planning the query**\n\nI should look at the sleep tables.")).toBe("Planning the query…");
    expect(nowLine([], "")).toBe("Getting started…");
    // A turn that was stopped doesn't leave a step working forever.
    const stopped = activityRows([{ kind: "command", id: "c", command: "python a.py", output: "", exitCode: null, status: "running" }], false);
    expect(stopped[0].type === "step" && stopped[0].step.tone).toBe("done");
  });

  it("folds three or more finished steps of one kind into a group", () => {
    const search = (id: string, query: string, status = "completed"): Item =>
      ({ ...(tool("search_catalog", { query }, { tables: [] }, status) as object), id }) as Item;
    const rows = activityRows(
      [search("a", "sleep"), search("b", "mood"), search("c", "phq"), search("d", "steps"), { kind: "message", id: "m", phase: "commentary", text: "Next." }, search("e", "x"), search("f", "y")],
      false,
    ); // prettier-ignore
    expect(rows.map((r) => r.type)).toEqual(["group", "say", "step", "step"]);
    const group = rows[0];
    expect(group.type === "group" && group.title).toBe("Searched 4 times");
    expect(group.type === "group" && group.steps).toHaveLength(4);
  });

  it("never hides a failed or running step inside a group", () => {
    const search = (id: string, status: string): Item =>
      ({ ...(tool("search_catalog", { query: id }, { tables: [] }, status) as object), id }) as Item;
    const rows = activityRows([search("a", "completed"), search("b", "completed"), search("c", "failed"), search("d", "completed")], false);
    expect(rows.map((r) => r.type)).toEqual(["step", "step", "step", "step"]);
  });

  it("leaves plans and helper questions to their cards", () => {
    expect(activityRows([tool("propose_plan", {}, null), tool("ask_research_helper", {}, null)], false)).toEqual([]);
  });
});

it("finds the tables a query reads, once each", () => {
  expect(tablesIn("select a from ihs_2025.fitbitdailydata f join IHS_2025.STUDYPARTICIPANTS p on 1=1 join ihs_2025.FitbitDailyData g on 1=1"))
    .toEqual(["IHS_2025.FITBITDAILYDATA", "IHS_2025.STUDYPARTICIPANTS"]); // prettier-ignore
  expect(tablesIn("SELECT 1 FROM DUAL")).toEqual([]);
});

it("names tables the way people say them", () => {
  expect(shortTable("IHS_2025.FITBITDAILYDATA")).toBe("FITBITDAILYDATA (2025)");
  expect(shortTable("OTHER")).toBe("OTHER");
});

// From a fork's review: a stopped turn showed its last commentary as "the answer".
describe("answerOf", () => {
  const said = { kind: "message" as const, id: "m", phase: "commentary" as const, text: "Next I'll check the sleep table." };
  it("gives a stopped or failed turn no answer", () => {
    expect(answerOf({ userText: "q", items: [said], status: "interrupted" })).toBe("");
    expect(answerOf({ userText: "q", items: [said], status: "failed" })).toBe("");
  });
  it("keeps a real answer, and a completed turn's last words", () => {
    const final = { ...said, id: "f", phase: "final_answer" as const, text: "The answer." };
    expect(answerOf({ userText: "q", items: [said, final], status: "interrupted" })).toBe("The answer.");
    expect(answerOf({ userText: "q", items: [said], status: "completed" })).toBe("Next I'll check the sleep table.");
  });
});
