// A panel over the page (the SQL and Workflows drawers and docked chats on a
// narrow window) closes on Escape, as on a click on the dimmed page beside it.
import { fireEvent, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { useEscapeToClose } from "./overlay";

function Panel({ active, onClose }: { active: boolean; onClose: () => void }) {
  useEscapeToClose(active, onClose);
  return <p>panel</p>;
}

it("closes on Escape while it's over the page, and not otherwise", () => {
  const onClose = vi.fn();
  const view = render(<Panel active onClose={onClose} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(window, { key: "Enter" });
  expect(onClose).toHaveBeenCalledTimes(1);
  view.rerender(<Panel active={false} onClose={onClose} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("leaves Escape to a dialog open on top of it", () => {
  const onClose = vi.fn();
  render(
    <>
      <Panel active onClose={onClose} />
      <div role="dialog">a dialog</div>
    </>,
  );
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).not.toHaveBeenCalled();
});
