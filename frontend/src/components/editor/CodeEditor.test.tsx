import { language } from "@codemirror/language";
import { forEachDiagnostic } from "@codemirror/lint";
import { getOriginalDoc } from "@codemirror/merge";
import { Text } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { diffSummary } from "./CodeMirrorDiff";
import { CodeEditor, type CodeEditorProps } from "./CodeEditor";
import { toRanges } from "./diagnostics";
import { DiffView } from "./DiffView";
import { changedRange } from "./sync";

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

it("keeps the cursor where it was when a new value arrives", async () => {
  const { rerender } = render(<CodeEditor label="SQL query" value={"SELECT a\nFROM t\nWHERE x = 1"} />);
  const { view } = await editor("SQL query");
  act(() => view.dispatch({ selection: { anchor: 13 } })); // in "FROM t", after the t
  rerender(<CodeEditor label="SQL query" value={"SELECT a, b\nFROM t\nWHERE x = 1"} />);
  expect(view.state.doc.toString()).toBe("SELECT a, b\nFROM t\nWHERE x = 1");
  expect(view.state.selection.main.head).toBe(16); // moved along by the ", b" before it
});

it("waits for an input method to finish before taking a new value", async () => {
  const { rerender } = render(<CodeEditor label="Notes" value="sleep" />);
  const { content, view } = await editor("Notes");
  fireEvent.compositionStart(content);
  rerender(<CodeEditor label="Notes" value="sleep and mood" />);
  expect(view.state.doc.toString()).toBe("sleep");
  fireEvent.compositionEnd(content);
  await waitFor(() => expect(view.state.doc.toString()).toBe("sleep and mood"));
});

it("changes only what differs", () => {
  expect(changedRange("SELECT a FROM t", "SELECT a FROM t")).toBeNull();
  expect(changedRange("SELECT a FROM t", "SELECT a, b FROM t")).toEqual({ from: 8, to: 8, insert: ", b" });
  expect(changedRange("aaa", "aa")).toEqual({ from: 2, to: 3, insert: "" });
  expect(changedRange("", "x")).toEqual({ from: 0, to: 0, insert: "x" });
  expect(changedRange("abc", "xyz")).toEqual({ from: 0, to: 3, insert: "xyz" });
});

it("indents with Tab, and lets Tab leave after Escape, without closing what's around it", async () => {
  const closed = vi.fn();
  const onWindowKey = (event: KeyboardEvent) => event.key === "Escape" && closed();
  window.addEventListener("keydown", onWindowKey);
  render(<CodeEditor label="Workflow" language="yaml" value="steps:" />);
  const { content, view } = await editor("Workflow");
  const tab = () => fireEvent.keyDown(content, { key: "Tab", code: "Tab", keyCode: 9 }); // false: the editor took it
  act(() => view.dispatch({ selection: { anchor: 0 } }));
  expect(tab()).toBe(false);
  expect(view.state.doc.toString()).toBe("  steps:");
  fireEvent.keyDown(content, { key: "Escape", code: "Escape", keyCode: 27 });
  expect(closed).not.toHaveBeenCalled(); // the editor's Escape stops there
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(tab()).toBe(true); // left to the browser, which moves focus on
  expect(tab()).toBe(true); // still, with no time limit
  expect(view.state.doc.toString()).toBe("  steps:");
  fireEvent.keyDown(content, { key: "ArrowRight", code: "ArrowRight", keyCode: 39 });
  expect(tab()).toBe(false); // any other key ends it
  window.removeEventListener("keydown", onWindowKey);
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
  // The changed line is marked + and − as well as by colour.
  const signs = [...document.querySelectorAll(".cm-diffSigns .cm-gutterElement")].map((e) => e.textContent).filter(Boolean);
  expect(signs).toEqual(["−", "+"]);
  // A new version updates the same editors: focus and scroll stay.
  const view = EditorView.findFromDOM(after)!;
  rerender(<DiffView label="clean.R" language="r" original={"x <- 1\ny <- 2"} modified={"x <- 1\ny <- 4\nz <- 5"} />);
  expect(await screen.findByText("2 lines added · 1 line removed")).toBeTruthy();
  expect(EditorView.findFromDOM(screen.getByRole("textbox", { name: "clean.R, after" }))).toBe(view);
  expect(view.state.doc.toString()).toBe("x <- 1\ny <- 4\nz <- 5");
  rerender(<DiffView label="clean.R" language="r" original={"x <- 1\ny <- 2"} modified={"x <- 1\ny <- 3"} layout="unified" />);
  const unified = await screen.findByRole("textbox", { name: "clean.R, changes" });
  const one = EditorView.findFromDOM(unified)!;
  expect(one.state.doc.toString()).toBe("x <- 1\ny <- 3");
  rerender(<DiffView label="clean.R" language="r" original={"x <- 0\ny <- 2"} modified={"x <- 1\ny <- 3"} layout="unified" />);
  expect(getOriginalDoc(one.state).toString()).toBe("x <- 0\ny <- 2");
  expect(EditorView.findFromDOM(screen.getByRole("textbox", { name: "clean.R, changes" }))).toBe(one);
  expect(screen.queryByRole("textbox", { name: "clean.R, before" })).toBeNull();
});
