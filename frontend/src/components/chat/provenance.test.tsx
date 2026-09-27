import { fireEvent, render, screen } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { expect, it, vi } from "vitest";

import { HowWasThisMade } from "@/features/workspace/HowWasThisMade";

import { NumberSources, type SourceLinks } from "./NumberSources";
import { asAnswerProvenance, type FileProvenance, markNumbers } from "./provenance";

function marked(markdown: string, numbers: string[]) {
  const { container } = render(
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[markNumbers(numbers)]}
      components={{ span: ({ node, ...props }) => <span {...props} /> }}
    >
      {markdown}
    </ReactMarkdown>,
  );
  return Array.from(container.querySelectorAll("[data-number]"), (e) => e.textContent);
}

it("marks the answer's numbers, whole, outside code and links", () => {
  const answer = [
    "Mean sleep was **7.2 h** in 81 participants, 12.5% missing.",
    "",
    "Not 181, 8.1, or 81.5; `81` in code and [81](https://x.org) aren't claims.",
    "",
    "| n | mean |",
    "|---|---|",
    "| 81 | 7.2 |",
  ].join("\n");
  expect(marked(answer, ["7.2", "81", "12.5%"])).toEqual(["7.2", "81", "12.5%", "81", "7.2"]);
  expect(marked("Nothing to mark: 3.", [])).toEqual([]);
});

it("reads a provenance event, dropping what isn't one", () => {
  expect(asAnswerProvenance({ answer: "m1", numbers: [{ text: "81", sources: [] }, { text: 3 }], files: ["outputs/a.png"] })).toEqual({
    answer: "m1",
    numbers: [{ text: "81", sources: [] }],
    more_numbers: 0,
    files: ["outputs/a.png"],
  });
  expect(asAnswerProvenance({ untraced: [] })).toBeUndefined();
});

const links: SourceLinks = {
  commandText: (id) => (id === "c1" ? "python analysis.py" : undefined),
  openQuery: vi.fn(),
  openFile: vi.fn(),
};

it("shows where a number appears, and says it only appears there", () => {
  render(
    <NumberSources
      text="81"
      sources={[
        { kind: "command", ref: "c1" },
        { kind: "query", ref: "q_0001" },
        { kind: "file", ref: "outputs/table.csv" },
      ]}
      links={links}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "81" }));
  const dialog = screen.getByRole("dialog", { name: "Where 81 appears" });
  expect(dialog).toHaveTextContent("python analysis.py");
  expect(dialog).toHaveTextContent("which doesn't show it was computed there");
  fireEvent.click(screen.getByRole("button", { name: "in the Queries tab" }));
  expect(links.openQuery).toHaveBeenCalledWith("q_0001");
  fireEvent.click(screen.getByRole("button", { name: "outputs/table.csv" }));
  expect(links.openFile).toHaveBeenCalledWith("outputs/table.csv");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("says plainly when a number appears nowhere", () => {
  render(<NumberSources text="1.3" sources={[]} links={links} />);
  fireEvent.click(screen.getByRole("button", { name: "1.3, which appears in nothing this turn produced" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("doesn't appear in any command output, query result, or output data file");
});

const made: FileProvenance = {
  path: "outputs/fig1.png",
  found: true,
  summary:
    "Written in turn 2 (its current content first appears in checkpoint 2). No command names it, but one ran a script that does (analysis.py). DataLab doesn't see which command writes a file inside the workspace.",
  checkpoint: 2,
  turn: 2,
  in_review: false,
  turn_not_saved: false,
  commands: [
    { id: "c2", command: "python /work/analysis.py", exit_code: 0, names_file: false, via_script: "analysis.py", seen_in_output: false },
    { id: "c1", command: "ls /data/oracle", exit_code: 0, names_file: false, via_script: null, seen_in_output: true },
  ],
  more_commands: 3,
  edited_directly: false,
  scripts: [{ path: "analysis.py", sha256: "a".repeat(64), names_file: true }],
  queries: [
    {
      id: "q_0001",
      started_at: "2026-09-27T10:30:00",
      tables: ["IHS_2025.VW_DAILY_MOOD"],
      row_count: 26405,
      result_file: "q_0001.csv",
      read_by: [{ kind: "script", ref: "analysis.py" }],
      in_turn: false,
    },
  ],
  more_queries: 0,
};

it("shows how a file was made, and opens its script and query", () => {
  const openQuery = vi.fn();
  const openScript = vi.fn();
  render(<HowWasThisMade provenance={made} openQuery={openQuery} openScript={openScript} />);
  expect(screen.getByText(/DataLab doesn't see which command writes a file/)).toBeInTheDocument();
  expect(screen.getByText("ran analysis.py, which names it")).toBeInTheDocument();
  expect(screen.getByText("And 3 more.")).toBeInTheDocument();
  expect(screen.getByText("Its result file is read by analysis.py.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "analysis.py" }));
  expect(openScript).toHaveBeenCalledWith("analysis.py", 2);
  fireEvent.click(screen.getByRole("button", { name: "q_0001" }));
  expect(openQuery).toHaveBeenCalledWith("q_0001");
});

it("says so when a file isn't in the latest checkpoint", () => {
  render(
    <HowWasThisMade
      provenance={{ ...made, found: false, summary: "This file isn't in the latest checkpoint.", commands: [], scripts: [], queries: [] }}
    />,
  );
  expect(screen.getByText("This file isn't in the latest checkpoint.")).toBeInTheDocument();
  expect(screen.queryByText(/Commands in turn/)).toBeNull();
});
