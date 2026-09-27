import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { expect, it } from "vitest";

import { glossaryTerm } from "@/lib/guide";

import { InfoTip } from "./InfoTip";

function Where() {
  const location = useLocation();
  return (
    <p>
      at {location.pathname}
      {location.hash} from {(location.state as { from?: string } | null)?.from}
    </p>
  );
}

function page(tip: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={["/workspace/c1"]}>
      <Routes>
        <Route
          path="workspace/:id"
          element={
            <>
              {tip}
              <button>Elsewhere</button>
            </>
          }
        />
        <Route path="help/:slug?" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

const rigor = glossaryTerm("rigor-review")!;

it("is a named button whose description is the glossary's text, in the page", () => {
  page(<InfoTip term="rigor-review" />);
  const trigger = screen.getByRole("button", { name: "About “Rigor review”" });
  expect(trigger).toHaveAccessibleDescription(rigor.short);
  expect(trigger).not.toHaveAttribute("title");
  // In the DOM even while closed, not only in a title attribute.
  expect(document.getElementById(trigger.getAttribute("aria-describedby")!)).toHaveTextContent(rigor.short);
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});

it("opens from the keyboard, reaches Learn more, and closes with Escape back on its button", () => {
  page(<InfoTip term="rigor-review" />);
  const trigger = screen.getByRole("button", { name: "About “Rigor review”" });
  trigger.focus();
  fireEvent.click(trigger); // Enter and Space on a button are a click
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  const tip = document.getElementById(trigger.getAttribute("aria-controls")!)!;
  const more = screen.getByRole("link", { name: "Learn more about “Rigor review” in Help" });
  expect(tip).toContainElement(more);
  // Next in the tab order after the button: the tip sits right after it.
  expect(trigger.compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

  more.focus();
  fireEvent.keyDown(more, { key: "Escape" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveFocus();
});

it("opens on hover, and a click elsewhere closes one opened by clicking", () => {
  page(<InfoTip term="checkpoint" />);
  const trigger = screen.getByRole("button", { name: "About “Checkpoint”" });
  fireEvent.mouseEnter(trigger.parentElement!);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  fireEvent.mouseLeave(trigger.parentElement!);
  expect(trigger).toHaveAttribute("aria-expanded", "false");

  fireEvent.click(trigger);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  fireEvent.pointerDown(screen.getByRole("button", { name: "Elsewhere" }));
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});

it("can use its own words as the trigger, as the session badge does", () => {
  page(<InfoTip term="data-session">Data session · database access, web blocked</InfoTip>);
  const trigger = screen.getByRole("button", { name: "Data session · database access, web blocked" });
  expect(trigger).toHaveAccessibleDescription(glossaryTerm("data-session")!.short);
});

it("goes to the term in Help, saying where it came from", () => {
  page(<InfoTip term="rigor-review" />);
  fireEvent.click(screen.getByRole("button", { name: "About “Rigor review”" }));
  fireEvent.click(screen.getByRole("link", { name: /Learn more/ }));
  expect(screen.getByText("at /help/glossary#rigor-review from /workspace/c1")).toBeInTheDocument();
});

it("hides a tip opened by hovering when Escape is pressed, wherever focus is (WCAG 1.4.13)", () => {
  page(<InfoTip term="checkpoint" />);
  const trigger = screen.getByRole("button", { name: "About “Checkpoint”" });
  const elsewhere = screen.getByRole("button", { name: "Elsewhere" });
  elsewhere.focus();
  fireEvent.mouseEnter(trigger.parentElement!);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  fireEvent.keyDown(elsewhere, { key: "Escape" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  // Focus stays where it was.
  expect(elsewhere).toHaveFocus();
});
