import { language } from "@codemirror/language";
import { forEachDiagnostic } from "@codemirror/lint";
import { Text } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { diffSummary } from "./CodeMirrorDiff";
import { CodeEditor, type CodeEditorProps } from "./CodeEditor";
import { toRanges } from "./diagnostics";
import { DiffView } from "./DiffView";

// jsdom has no layout. CodeMirror measures text; give it empty boxes.
for (const proto of [Range.prototype, Element.prototype]) {
  proto.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as DOMRectList;
}
Range.prototype.getBoundingClientRect = () => new DOMRect();

/** The editor, once its chunk has loaded. */
async function editor(label: string) {
  const content = await screen.findByRole("textbox", { name: label });
  return { content, view: EditorView.findFromDOM(content)! };
}

function Controlled(props: Partial<CodeEditorProps> & { initial: string; onValue?: (value: string) => void }) {
  const [value, setValue] = useState(props.initial);
  return (
    <>
      <CodeEditor
        label="SQL query"
        language="sql"
        {...props}
        value={value}
        onChange={(next) => {
          setValue(next);
          props.onValue?.(next);
        }}
      />
      <button onClick={() => setValue("SELECT 2 FROM dual")}>Replace</button>
    </>
  );
}

it("shows the text while the editor loads, then the editor, labelled", async () => {
  render(<CodeEditor label="SQL query" value="SELECT 1 FROM dual" />);
  expect(screen.getByLabelText("SQL query (loading the editor)")).toHaveTextContent("SELECT 1 FROM dual");
  const { content, view } = await editor("SQL query");
  expect(view.state.doc.toString()).toBe("SELECT 1 FROM dual");
  expect(content).toHaveAttribute("aria-readonly", "false");
  expect(content).toHaveAccessibleDescription(/Escape, then Tab/);
});

it("reports what's typed, and takes a new value without reporting it back", async () => {
  const onValue = vi.fn();
  render(<Controlled initial="SELECT 1" onValue={onValue} />);
  const { view } = await editor("SQL query");
  act(() => view.dispatch({ changes: { from: 8, insert: " FROM dual" }, userEvent: "input.type" }));
  expect(onValue).toHaveBeenLastCalledWith("SELECT 1 FROM dual");
  fireEvent.click(screen.getByText("Replace"));
  expect(view.state.doc.toString()).toBe("SELECT 2 FROM dual");
  expect(onValue).toHaveBeenCalledTimes(1);
});

it("can be read-only", async () => {
  const { rerender } = render(<CodeEditor label="Workflow" value="steps: []" language="yaml" readOnly />);
  const { content, view } = await editor("Workflow");
  expect(view.state.readOnly).toBe(true);
  expect(content).toHaveAttribute("aria-readonly", "true");
  expect(content).toHaveAccessibleDescription("Read-only.");
  rerender(<CodeEditor label="Workflow" value="steps: []" language="yaml" />);
  expect(view.state.readOnly).toBe(false);
});

it("loads each language, and switches between them", async () => {
  const { rerender } = render(<CodeEditor label="Code" value="x <- 1" language="r" />);
  const { view } = await editor("Code");
  await waitFor(() => expect(view.state.facet(language)?.name).toBe("r"));
  for (const [name, expected] of [["sql", "sql"], ["yaml", "yaml"], ["markdown", "markdown"]] as const) {
    rerender(<CodeEditor label="Code" value="x <- 1" language={name} />);
    await waitFor(() => expect(view.state.facet(language)?.name).toBe(expected));
  }
  rerender(<CodeEditor label="Code" value="x <- 1" language="text" />);
  await waitFor(() => expect(view.state.facet(language)).toBeNull());
});

it("marks the problems it's given, and says the text is invalid while any is an error", async () => {
  const { rerender } = render(
    <CodeEditor
      label="SQL query"
      language="sql"
      value={"SELECT *\nFROM fitbit_daily\nWHERE x"}
      diagnostics={[{ line: 2, column: 6, message: "No table called FITBIT_DAILY.", severity: "error" }]}
    />,
  );
  const { content, view } = await editor("SQL query");
  const marked = () => {
    const found: { text: string; severity: string; message: string }[] = [];
    forEachDiagnostic(view.state, (d, from, to) =>
      found.push({ text: view.state.sliceDoc(from, to), severity: d.severity, message: d.message }),
    );
    return found;
  };
  expect(marked()).toEqual([{ text: "fitbit_daily", severity: "error", message: "No table called FITBIT_DAILY." }]);
  expect(content).toHaveAttribute("aria-invalid", "true");
  rerender(
    <CodeEditor
      label="SQL query"
      language="sql"
      value={"SELECT *\nFROM fitbit_daily\nWHERE x"}
      diagnostics={[{ line: 3, message: "Consider a date filter.", severity: "warning" }]}
    />,
  );
  expect(marked()).toEqual([{ text: "WHERE", severity: "warning", message: "Consider a date filter." }]);
  expect(content).not.toHaveAttribute("aria-invalid");
});

it("runs onSubmit on Ctrl+Enter, instead of adding a line", async () => {
  const onSubmit = vi.fn();
  render(<CodeEditor label="SQL query" value="SELECT 1" onSubmit={onSubmit} />);
  const { content, view } = await editor("SQL query");
  fireEvent.keyDown(content, { key: "Enter", code: "Enter", keyCode: 13, ctrlKey: true });
  expect(onSubmit).toHaveBeenCalledTimes(1);
  expect(view.state.doc.toString()).toBe("SELECT 1");
});

it("places line-and-column problems in the text, even past its end", () => {
  const doc = Text.of(["SELECT a,", "  b FROM t", ""]);
  const ranges = (line: number, column?: number) =>
    toRanges(doc, [{ line, column, message: "m", severity: "error" }]).map((d) => doc.sliceString(d.from, d.to));
  expect(ranges(1)).toEqual(["SELECT"]);
  expect(ranges(1, 9)).toEqual([","]); // no word there: one character
  expect(ranges(2, 3)).toEqual(["b"]);
  expect(ranges(2, 99)).toEqual([""]); // past the end of the line: its end
  expect(ranges(40)).toEqual([""]); // past the last line: the last line
  expect(ranges(0, 0)).toEqual(["SELECT"]);
});

it("counts the lines a diff adds and removes", () => {
  expect(diffSummary("a\nb\nc", "a\nb\nc")).toEqual({ added: 0, removed: 0 });
  expect(diffSummary("a\nb\nc", "a\nB\nc\nd")).toEqual({ added: 2, removed: 1 });
  expect(diffSummary("a\nb\nc\nd", "a\nd")).toEqual({ added: 0, removed: 2 });
});

it("shows a diff side by side, or in one column, read-only and labelled", async () => {
  const { rerender } = render(<DiffView label="clean.R" language="r" original={"x <- 1\ny <- 2"} modified={"x <- 1\ny <- 3"} />);
  expect(await screen.findByText("1 line added · 1 line removed")).toBeTruthy();
  const before = await screen.findByRole("textbox", { name: "clean.R, before" });
  const after = screen.getByRole("textbox", { name: "clean.R, after" });
  expect(EditorView.findFromDOM(before)!.state.doc.toString()).toBe("x <- 1\ny <- 2");
  expect(EditorView.findFromDOM(after)!.state.readOnly).toBe(true);
  rerender(<DiffView label="clean.R" language="r" original={"x <- 1\ny <- 2"} modified={"x <- 1\ny <- 3"} layout="unified" />);
  const unified = await screen.findByRole("textbox", { name: "clean.R, changes" });
  expect(EditorView.findFromDOM(unified)!.state.doc.toString()).toBe("x <- 1\ny <- 3");
  expect(screen.queryByRole("textbox", { name: "clean.R, before" })).toBeNull();
});
