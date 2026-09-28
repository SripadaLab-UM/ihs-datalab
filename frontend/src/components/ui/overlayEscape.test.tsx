// Escape inside a drawer closes the innermost thing first: a glossary tip in it, or a dialog over it.
import { fireEvent, render, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";

import { InfoTip } from "./InfoTip";
import { useOverlay } from "./overlay";

function Drawer({ over = false }: { over?: boolean }) {
  const [open, setOpen] = useState(true);
  const box = useRef<HTMLElement>(null);
  useOverlay(box, open, () => setOpen(false));
  return (
    <MemoryRouter>
      {open && (
        <aside ref={box} role="dialog" aria-label="Chat drawer" tabIndex={-1}>
          <button>First</button>
          <InfoTip term="workflow" />
        </aside>
      )}
      {over && <div role="dialog" aria-label="Over it" />}
    </MemoryRouter>
  );
}

const tip = () => screen.getByRole("button", { name: /About “Workflow”/ });

it("closes an open tip first, then the drawer", () => {
  render(<Drawer />);
  fireEvent.click(tip());
  expect(tip()).toHaveAttribute("aria-expanded", "true");
  fireEvent.keyDown(tip(), { key: "Escape" });
  expect(tip()).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("dialog", { name: "Chat drawer" })).toBeInTheDocument();
  fireEvent.keyDown(tip(), { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Chat drawer" })).toBeNull();
});

it("closes a tip that's only hovered, with focus elsewhere in the drawer, before the drawer", () => {
  render(<Drawer />);
  fireEvent.mouseEnter(tip().parentElement!);
  expect(tip()).toHaveAttribute("aria-expanded", "true");
  const first = screen.getByRole("button", { name: "First" });
  fireEvent.keyDown(first, { key: "Escape" });
  expect(tip()).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("dialog", { name: "Chat drawer" })).toBeInTheDocument();
  fireEvent.keyDown(first, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Chat drawer" })).toBeNull();
});

it("leaves the drawer open while another dialog is open over it", () => {
  render(<Drawer over />);
  fireEvent.keyDown(screen.getByRole("button", { name: "First" }), { key: "Escape" });
  expect(screen.getByRole("dialog", { name: "Chat drawer" })).toBeInTheDocument();
});
