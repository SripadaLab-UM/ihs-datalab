import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { CodeBlock, HighlightedCode } from "./CodeBlock";
import { CodeDiff } from "./CodeDiff";
import { codeLanguage, isCodeFile, languageOfPath, MAX_HIGHLIGHT_CHARS } from "./languages";

afterEach(() => {
  delete document.documentElement.dataset.theme;
});

const highlighted = async () => {
  const pre = document.querySelector("pre")!;
  await waitFor(() => expect(pre).toHaveAttribute("data-highlighted", "true"));
  return pre;
};

it.each([
  ["sql", "SELECT id FROM ihs_2025.steps -- steps\n", "SELECT", "-- steps"],
  ["r", "steps <- function(x) { # weekly\n  mean(x)\n}", "function", "# weekly"],
  ["python", "import pandas as pd\n# load\nx = 'a'", "import", "# load"],
  ["shell", "for f in *.csv; do wc -l \"$f\"; done", "for", null],
] as const)("highlights %s with token classes", async (language, code, keyword, comment) => {
  render(<CodeBlock code={code} language={language} />);
  const pre = await highlighted();
  expect([...pre.querySelectorAll(".tok-keyword")].map((node) => node.textContent)).toContain(keyword);
  if (comment) expect([...pre.querySelectorAll(".tok-comment")].map((node) => node.textContent)).toContain(comment);
  // The text itself is unchanged.
  expect(pre.textContent).toBe(code.replace(/\n$/, ""));
});

it.each(["light", "dark"])("uses the same classes in the %s theme (the colours are the theme's tokens)", async (theme) => {
  document.documentElement.dataset.theme = theme;
  render(<CodeBlock code={"x <- 'a' # note"} language="r" />);
  const pre = await highlighted();
  expect(pre.querySelector(".tok-string")).toHaveTextContent("'a'");
  expect(pre.querySelector(".tok-comment")).toHaveTextContent("# note");
});

it("shows code as text, never as markup", async () => {
  const code = `print("<img src=x onerror=alert(1)><script>alert(2)</script>")`;
  render(<CodeBlock code={code} language="python" />);
  const pre = await highlighted();
  expect(pre.querySelector("img, script")).toBeNull();
  expect(pre).toHaveTextContent(code);
});

it("shows very long code as plain text, and says why", async () => {
  const code = "x <- 1\n".repeat(Math.ceil(MAX_HIGHLIGHT_CHARS / 7) + 10);
  render(<CodeBlock code={code} language="r" />);
  expect(screen.getByText(/too long to highlight/)).toBeInTheDocument();
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(document.querySelector("pre")).toHaveAttribute("data-highlighted", "false");
  expect(document.querySelector(".tok-keyword, .tok-variableName")).toBeNull();
});

it("leaves code in no known language as it is", async () => {
  render(<CodeBlock code="just text" language="text" />);
  expect(document.querySelector("pre")).toHaveTextContent("just text");
  expect(screen.queryByText(/too long/)).toBeNull();
});

it("numbers lines when asked", async () => {
  render(<CodeBlock code={"a <- 1\nb <- 2"} language="r" lineNumbers />);
  await highlighted();
  expect(document.querySelector("pre")).toHaveTextContent(/1\s*a <- 1\s*2\s*b <- 2/);
});

it("highlights inside a code element of one's own (a Markdown block)", async () => {
  render(
    <pre>
      <code>
        <HighlightedCode code="SELECT 1 FROM dual" language="sql" />
      </code>
    </pre>,
  );
  await waitFor(() => expect(document.querySelector(".tok-keyword")).toHaveTextContent("SELECT"));
});

it("marks a unified diff's lines, each highlighted as its own version has it", async () => {
  render(
    <CodeDiff
      label="a.py"
      language="python"
      baseText={"import os\nx = 1\n"}
      headText={"import os\nx = 2\n"}
      lines={[
        { op: "@", old: null, new: null, text: "@@ -1,2 +1,2 @@" },
        { op: " ", old: 1, new: 1, text: "import os" },
        { op: "-", old: 2, new: null, text: "x = 1" },
        { op: "+", old: null, new: 2, text: "x = 2" },
      ]}
    />,
  );
  expect(screen.getByText("Lines 1–2 → 1–2")).toBeInTheDocument();
  await waitFor(() => expect(document.querySelector("[data-op=same] .tok-keyword")).toHaveTextContent("import"));
  expect(document.querySelector("[data-op=removed]")).toHaveTextContent("x = 1");
  expect(document.querySelector("[data-op=added] .tok-number")).toHaveTextContent("2");
});

it("knows languages by name and by file", () => {
  expect(codeLanguage("R")).toBe("r");
  expect(codeLanguage("bash")).toBe("shell");
  expect(codeLanguage("plsql")).toBe("sql");
  expect(codeLanguage("cobol")).toBe("text");
  expect(languageOfPath("scripts/a.py")).toBe("python");
  expect(languageOfPath("notes.txt")).toBe("text");
  expect(isCodeFile("outputs/analysis.R")).toBe(true);
  expect(isCodeFile("steps.ipynb")).toBe(true);
  expect(isCodeFile("outputs/report.html")).toBe(false);
});

it("never renders code through dangerouslySetInnerHTML", () => {
  const sources = import.meta.glob(["./*.tsx", "./*.ts", "../../features/workspace/Code.tsx", "!./*.test.tsx"], {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;
  expect(Object.keys(sources).length).toBeGreaterThanOrEqual(5);
  for (const [name, source] of Object.entries(sources)) {
    expect(source, name).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
  }
});
