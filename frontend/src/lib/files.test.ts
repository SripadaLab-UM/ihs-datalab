import { expect, it } from "vitest";

import { answerFileLinks, containerPath, fileNoun, kindOf, workspaceFile } from "./files";

it("maps container paths to viewer roots", () => {
  expect(workspaceFile("/work/outputs/figures/a.png")).toEqual({ root: "outputs", path: "figures/a.png", kind: "image" });
  expect(workspaceFile("/work/analysis.py")).toEqual({ root: "work", path: "analysis.py", kind: "text" });
  expect(workspaceFile("/data/oracle/q_1.csv")).toEqual({ root: "results", path: "q_1.csv", kind: "csv" });
  expect(workspaceFile("/work/outputs/my%20report.html#top")?.path).toBe("my report.html");
  expect(workspaceFile("/work/outputs/report.html:22")?.path).toBe("report.html");
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

it("names a file by what it is", () => {
  const noun = (path: string) => fileNoun(workspaceFile(path)!);
  expect(noun("/work/outputs/report.html")).toBe("report");
  expect(noun("/work/outputs/report.pdf")).toBe("report (PDF)");
  expect(noun("/work/outputs/fig.png")).toBe("plot");
  expect(noun("/work/outputs/fig.svg")).toBe("plot");
  expect(noun("/work/outputs/t.csv")).toBe("table (CSV)");
  expect(noun("/data/oracle/q_1.csv")).toBe("query results (CSV)");
  expect(noun("/work/a.py")).toBe("Python script");
  expect(noun("/work/a.rds")).toBe("file");
});

it("links only the answer's known files, once each, outside code blocks", () => {
  const known = new Set(["/work/outputs/report.html", "/work/outputs/a.csv"]);
  const text = "`/work/outputs/report.html` `/work/outputs/report.html` `/work/outputs/b.csv`\n\n```\n`/work/outputs/a.csv`\n```";
  const links = answerFileLinks(text, known);
  expect([...links.keys()]).toEqual(["/work/outputs/report.html"]);
  expect(links.get("/work/outputs/report.html")?.label).toBe("Open report");
  expect(answerFileLinks("[x](/work/outputs/../../etc/passwd)", new Set(["/etc/passwd"])).size).toBe(0);
  expect(containerPath({ root: "results", path: "q_1.csv" })).toBe("/data/oracle/q_1.csv");
});
