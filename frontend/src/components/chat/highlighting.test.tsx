// Code in the chat is highlighted: answers' fenced blocks (the folded SQL too),
// and the commands in "How this answer was made", which the Code tab can open at.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it } from "vitest";

import { GuideMarkdown } from "@/features/help/GuideMarkdown";

import type { Row, Step } from "./activity";
import { Markdown } from "./Markdown";
import { ShownStepContext } from "./showStep";
import { GroupRow, StepRow } from "./Story";

it("highlights an answer's fenced code, as text", async () => {
  const { container } = render(<Markdown text={"Run:\n\n```r\nsteps <- read.csv('a.csv') # load\n```\n\n```\nplain\n```"} />);
  await waitFor(() => expect(container.querySelector("code.language-r .tok-comment")).toHaveTextContent("# load"));
  expect(container.querySelector("code.language-r .tok-string")).toHaveTextContent("'a.csv'");
  // A block with no language stays as it is.
  expect(screen.getByText("plain").querySelector("span")).toBeNull();
});

it("highlights the SQL an answer quotes, folded away", async () => {
  const { container } = render(<Markdown answer text={"```sql\nSELECT COUNT(*) FROM ihs_2025.steps\n```"} />);
  fireEvent.click(screen.getByText(/SQL the answer quotes/));
  await waitFor(() => expect(container.querySelector("details code .tok-keyword")).toHaveTextContent("SELECT"));
});

it("highlights code in the guide", async () => {
  const { container } = render(<GuideMarkdown page={{ slug: "x" }} text={"```sh\nls /work\n```"} />);
  await waitFor(() => expect(container.querySelector("code.language-sh span[class^=tok-]")).not.toBeNull());
});

const command = (id: string, text: string): Step => ({
  key: `cmd-${id}`,
  icon: "code",
  title: `Ran a short Python snippet ${id}`,
  chips: [],
  tone: "done",
  detail: { kind: "command", command: text, output: "", exitCode: 0 },
});

it("highlights a step's command, and opens at the step asked for", async () => {
  const step = command("c1", "python3 -c 'import os'");
  const { rerender, container } = render(
    <ShownStepContext value={null}>
      <StepRow step={step} />
    </ShownStepContext>,
  );
  expect(screen.getByRole("button", { name: /Ran a short Python snippet c1/ })).toHaveAttribute("aria-expanded", "false");
  rerender(
    <ShownStepContext value={{ key: "cmd-c1", n: 1 }}>
      <StepRow step={step} />
    </ShownStepContext>,
  );
  expect(screen.getByRole("button", { name: /Ran a short Python snippet c1/ })).toHaveAttribute("aria-expanded", "true");
  expect(document.activeElement).toHaveAttribute("id", "step-cmd-c1");
  await waitFor(() => expect(container.querySelector("pre[data-language=shell]")).toHaveAttribute("data-highlighted", "true"));
});

it("opens a folded group onto the step asked for", async () => {
  const steps = ["a", "b", "c"].map((id) => command(id, `python3 -c 'print(${id})'`));
  const row: Extract<Row, { type: "group" }> = { type: "group", key: "g", icon: "code", title: "Ran 3 commands", chips: [], steps };
  render(
    <ShownStepContext value={{ key: "cmd-b", n: 1 }}>
      <GroupRow row={row} />
    </ShownStepContext>,
  );
  await act(async () => {});
  expect(screen.getByRole("button", { name: /Ran 3 commands/ })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: /snippet b/ })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: /snippet a/ })).toHaveAttribute("aria-expanded", "false");
});
