import { expect, it } from "vitest";

import { kindOf, workspaceFile } from "./files";

it("maps container paths to viewer roots", () => {
  expect(workspaceFile("/work/outputs/figures/a.png")).toEqual({ root: "outputs", path: "figures/a.png", kind: "image" });
  expect(workspaceFile("/work/analysis.py")).toEqual({ root: "work", path: "analysis.py", kind: "text" });
  expect(workspaceFile("/data/oracle/q_1.csv")).toEqual({ root: "results", path: "q_1.csv", kind: "csv" });
  expect(workspaceFile("/work/outputs/my%20report.html#top")?.path).toBe("my report.html");
});

it("ignores everything else", () => {
  expect(workspaceFile("https://example.org/work/x")).toBeNull();
  expect(workspaceFile("/work/../etc/passwd")).toBeNull();
  expect(workspaceFile("/inputs/x.csv")).toBeNull();
  expect(workspaceFile("outputs/x.csv")).toBeNull();
});

it("knows file kinds", () => {
  expect(kindOf("a.HTML")).toBe("html");
  expect(kindOf("a.tsv")).toBe("csv");
  expect(kindOf("a.rds")).toBe("other");
});
