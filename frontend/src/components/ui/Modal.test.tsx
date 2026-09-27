import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";

import { Modal } from "./index";

// From the design review: focus stayed on the page behind an open dialog.
function Page({ autoFocus = false }: { autoFocus?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Export</button>
      <button>Behind</button>
      {open && (
        <Modal title="Export" onClose={() => setOpen(false)}>
          <input aria-label="First" autoFocus={autoFocus} />
          <button>Last</button>
        </Modal>
      )}
    </>
  );
}

it("takes focus in, keeps it there, makes the page inert, and gives it back", () => {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  render(<Page />, { container: root });
  const opener = screen.getByText("Export", { selector: "button" });
  opener.focus();
  fireEvent.click(opener);

  const dialog = screen.getByRole("dialog", { name: "Export" });
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(root.hasAttribute("inert")).toBe(true);

  // Tab from the last control wraps to the first, never to the page behind.
  const close = screen.getByRole("button", { name: "Close" });
  const last = screen.getByText("Last");
  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(last);

  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(root.hasAttribute("inert")).toBe(false);
  expect(document.activeElement).toBe(opener);
  root.remove();
});

it("gives focus back even when a field in the dialog took it first (autoFocus)", () => {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  render(<Page autoFocus />, { container: root });
  const opener = screen.getByText("Export", { selector: "button" });
  opener.focus();
  fireEvent.click(opener);
  expect(document.activeElement).toBe(screen.getByLabelText("First"));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(document.activeElement).toBe(opener);
  root.remove();
});
