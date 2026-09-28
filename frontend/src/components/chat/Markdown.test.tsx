import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";

import { KnownFilesContext, OpenFileContext } from "@/lib/files";

import { Markdown } from "./Markdown";
import { ShowQueryContext } from "./provenance";

const KNOWN = new Set([
  "/work/outputs/report.html",
  "/work/outputs/pilot_study_flow.csv",
  "/work/outputs/pilot_study_model.csv",
  "/work/outputs/figures/sleep.png",
  "/work/analysis.R",
  "/data/oracle/q_20260926T194554_b2e2dd.csv",
]);

function Workspace({ children, open = vi.fn(), query = null }: { children: ReactNode; open?: () => void; query?: ((id: string) => void) | null }) {
  return (
    <OpenFileContext value={open}>
      <KnownFilesContext value={KNOWN}>
        <ShowQueryContext value={query}>{children}</ShowQueryContext>
      </KnownFilesContext>
    </OpenFileContext>
  );
}

// From the design review: answers showed long internal paths; they read as what the file is.
it("shows a known output as a readable button, with its path in the tooltip", () => {
  const open = vi.fn();
  render(
    <Workspace open={open}>
      <Markdown text={"See `/work/outputs/report.html` and run `SELECT 1`; not `/etc/passwd`."} />
    </Workspace>,
  );
  const button = screen.getByRole("button", { name: "Open report" });
  expect(button).toHaveAttribute("title", "/work/outputs/report.html");
  expect(screen.queryByText("/work/outputs/report.html")).toBeNull();
  fireEvent.click(button);
  expect(open).toHaveBeenCalledWith({ root: "outputs", path: "report.html", kind: "html" });
  expect(screen.getByText("SELECT 1").tagName).toBe("CODE");
  expect(screen.getByText("/etc/passwd").tagName).toBe("CODE");
});

it("names files by kind, and tells files of one kind apart by name", () => {
  render(
    <Workspace>
      <Markdown
        text={
          "Tables: `/work/outputs/pilot_study_flow.csv`, `/work/outputs/pilot_study_model.csv`. " +
          "Plot: `/work/outputs/figures/sleep.png`. Source: `/work/analysis.R`."
        }
      />
    </Workspace>,
  );
  expect(screen.getByRole("button", { name: "Open table: flow (CSV)" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open table: model (CSV)" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open plot" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open R script" })).toBeInTheDocument();
});

it("never links a path this conversation doesn't have", () => {
  render(
    <Workspace>
      <Markdown text={"`/work/outputs/other.html` and [secret](/work/outputs/secret.html) and [x](/data/oracle/../etc)"} />
    </Workspace>,
  );
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText("/work/outputs/other.html").tagName).toBe("CODE");
  expect(screen.getByText("secret").tagName).toBe("SPAN");
});

it("links nothing where the conversation's files aren't known", () => {
  render(
    <OpenFileContext value={vi.fn()}>
      <Markdown text={"`/work/outputs/report.html` [the report](/work/outputs/report.html)"} />
    </OpenFileContext>,
  );
  expect(screen.queryByRole("button")).toBeNull();
});

it("opens a known file from a Markdown link, keeping the link's words", () => {
  const open = vi.fn();
  render(
    <Workspace open={open}>
      <Markdown text={"Read [the pilot report](/work/outputs/report.html)."} />
    </Workspace>,
  );
  const button = screen.getByRole("button", { name: "the pilot report" });
  expect(button).toHaveAttribute("title", "/work/outputs/report.html");
  fireEvent.click(button);
  expect(open).toHaveBeenCalledWith({ root: "outputs", path: "report.html", kind: "html" });
});

it("leaves code blocks, globs and folders as code", () => {
  render(
    <Workspace>
      <Markdown text={"```\n/work/outputs/report.html\n```\n\n`/work/outputs/*.csv` `/work/outputs/figs/`"} />
    </Workspace>,
  );
  expect(screen.queryByRole("button")).toBeNull();
});

it("doesn't show query ids: a known one opens the Queries tab", () => {
  const query = vi.fn();
  render(
    <Workspace query={query}>
      <Markdown text={"Mood (`q_20260926T194554_b2e2dd`, 1,012 rows) and sleep (`q_20260926T194550_2d95e3`)."} />
    </Workspace>,
  );
  expect(screen.queryByText(/q_2026/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show query" }));
  expect(query).toHaveBeenCalledWith("q_20260926T194554_b2e2dd");
  // Not this conversation's: named, not linked.
  expect(screen.getByText("a query")).toHaveAttribute("title", "Query q_20260926T194550_2d95e3");
});

it("folds SQL away in an answer, but not elsewhere", () => {
  const text = "Done.\n\n```sql\nSELECT COUNT(*) FROM IHS_2025.X\n```";
  const { container, unmount } = render(
    <Workspace>
      <Markdown text={text} answer />
    </Workspace>,
  );
  const details = container.querySelector("details");
  expect(details).not.toBeNull();
  expect(details).not.toHaveAttribute("open");
  unmount();
  const guide = render(
    <Workspace>
      <Markdown text={text} />
    </Workspace>,
  );
  expect(guide.container.querySelector("details")).toBeNull();
});
