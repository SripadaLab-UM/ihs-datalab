import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { OpenFileContext } from "@/lib/files";

import { Markdown } from "./Markdown";

// From the design review: output paths in answers showed as code, not as something to open.
it("opens a workspace path written as code, and leaves other code alone", () => {
  const open = vi.fn();
  render(
    <OpenFileContext value={open}>
      <Markdown text={"See `/work/outputs/report.html` and run `SELECT 1`; not `/etc/passwd`."} />
    </OpenFileContext>,
  );
  fireEvent.click(screen.getByRole("button", { name: "/work/outputs/report.html" }));
  expect(open).toHaveBeenCalledWith({ root: "outputs", path: "report.html", kind: "html" });
  expect(screen.getByText("SELECT 1").tagName).toBe("CODE");
  expect(screen.getByText("/etc/passwd").tagName).toBe("CODE");
});

it("leaves code blocks, globs and folders as code", () => {
  render(
    <OpenFileContext value={vi.fn()}>
      <Markdown text={"```\n/work/outputs/a.csv\n```\n\n`/work/outputs/*.csv` `/work/outputs/figs/`"} />
    </OpenFileContext>,
  );
  expect(screen.queryByRole("button")).toBeNull();
});
