import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { BIG_STEP, defaultWidths, fit, LIMITS, STEP, usePanels } from "./panels";

function Harness({ tab = "sql", chatOpen = true }: { tab?: string; chatOpen?: boolean }) {
  const panels = usePanels(tab, { chatOpen });
  return (
    <div ref={panels.ref} data-testid="panels" style={panels.style}>
      <aside>list</aside>
      <main>page</main>
      {panels.dividers}
    </div>
  );
}

const wide = (matches: boolean) => {
  window.matchMedia = vi.fn(() => ({ matches, addEventListener() {}, removeEventListener() {} })) as never;
};
const windowWidth = (width: number) => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  act(() => void window.dispatchEvent(new Event("resize")));
};
const nav = () => screen.getByRole("separator", { name: "Resize the list" });
const chat = () => screen.getByRole("separator", { name: "Resize the chat" });
const now = (el: HTMLElement) => Number(el.getAttribute("aria-valuenow"));

beforeEach(() => {
  localStorage.clear();
  wide(true);
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1400 });
});
afterEach(() => vi.restoreAllMocks());

it("is a focusable vertical separator with its value and range, and the grid follows it", () => {
  render(<Harness />);
  const divider = nav();
  expect(divider).toHaveAttribute("aria-orientation", "vertical");
  expect(divider).toHaveAttribute("tabindex", "0");
  expect(divider.className).toMatch(/\bcursor-col-resize\b/);
  expect(now(divider)).toBe(272);
  expect(divider).toHaveAttribute("aria-valuemin", String(LIMITS.nav.min));
  expect(now(chat())).toBe(416);
  expect(screen.getByTestId("panels").style.gridTemplateColumns).toBe("272px minmax(0,1fr) 416px");
});

it("moves with the arrow keys (Shift for bigger steps), Home and End, and Enter puts it back", () => {
  render(<Harness />);
  fireEvent.keyDown(nav(), { key: "ArrowRight" });
  expect(now(nav())).toBe(272 + STEP);
  fireEvent.keyDown(nav(), { key: "ArrowRight", shiftKey: true });
  expect(now(nav())).toBe(272 + STEP + BIG_STEP);
  fireEvent.keyDown(nav(), { key: "ArrowLeft" });
  expect(now(nav())).toBe(272 + BIG_STEP);
  fireEvent.keyDown(nav(), { key: "Home" });
  expect(now(nav())).toBe(LIMITS.nav.min);
  fireEvent.keyDown(nav(), { key: "End" });
  expect(now(nav())).toBe(LIMITS.nav.max);
  fireEvent.keyDown(nav(), { key: "Enter" });
  expect(now(nav())).toBe(272);
  // The chat's divider grows the chat as it moves left.
  fireEvent.keyDown(chat(), { key: "ArrowLeft" });
  expect(now(chat())).toBe(416 + STEP);
  fireEvent.doubleClick(chat());
  expect(now(chat())).toBe(416);
});

it("drags with the pointer", () => {
  render(<Harness />);
  fireEvent.pointerDown(nav(), { button: 0, clientX: 272, pointerId: 1 });
  expect(document.body.style.userSelect).toBe("none");
  fireEvent.pointerMove(nav(), { clientX: 330, pointerId: 1 });
  expect(now(nav())).toBe(330);
  fireEvent.pointerUp(nav(), { clientX: 330, pointerId: 1 });
  expect(document.body.style.userSelect).toBe("");
  fireEvent.pointerDown(chat(), { button: 0, clientX: 900, pointerId: 2 });
  fireEvent.pointerMove(chat(), { clientX: 860, pointerId: 2 });
  fireEvent.pointerUp(chat(), { pointerId: 2 });
  expect(now(chat())).toBe(456);
});

it("keeps within its limits and leaves the page its minimum, as the window narrows and widens", () => {
  render(<Harness />);
  fireEvent.keyDown(chat(), { key: "End" });
  // 1400 - 272 - 420
  expect(now(chat())).toBe(708);
  windowWidth(1100);
  // The chat gives way first...
  expect(now(chat())).toBe(1100 - 272 - LIMITS.content);
  expect(now(nav())).toBe(272);
  // ...down to its minimum, then the list.
  windowWidth(1000);
  expect(now(chat())).toBe(LIMITS.chat.min);
  expect(now(nav())).toBe(1000 - LIMITS.chat.min - LIMITS.content);
  // Widened again, the widths come back as they were set.
  windowWidth(1400);
  expect(now(chat())).toBe(708);
  expect(now(nav())).toBe(272);
  expect(fit(1000, { nav: 300, chat: 400 }, { nav: true, chat: true })).toMatchObject({ nav: 260, chat: 320 });
  expect(defaultWidths(1600)).toEqual({ nav: 288, chat: 480 });
});

it("remembers the widths for each tab separately, and Reset panel widths forgets them", () => {
  const { unmount } = render(<Harness tab="sql" />);
  fireEvent.keyDown(nav(), { key: "ArrowRight" });
  fireEvent.keyDown(chat(), { key: "ArrowLeft", shiftKey: true });
  expect(JSON.parse(localStorage.getItem("datalab:panels:sql")!)).toEqual({ nav: 288, chat: 480 });
  unmount();
  const other = render(<Harness tab="knowledge" />);
  expect(now(nav())).toBe(272);
  other.unmount();
  render(<Harness tab="sql" />);
  expect(now(nav())).toBe(288);
  expect(now(chat())).toBe(480);
  fireEvent.click(screen.getAllByRole("button", { name: "Reset panel widths" })[0]);
  expect(now(nav())).toBe(272);
  expect(now(chat())).toBe(416);
  expect(localStorage.getItem("datalab:panels:sql")).toBeNull();
});

it("still resizes when storage is refused", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("refused");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("refused");
  });
  render(<Harness />);
  expect(now(nav())).toBe(272);
  fireEvent.keyDown(nav(), { key: "ArrowRight" });
  expect(now(nav())).toBe(288);
});

it("ignores stored widths that aren't numbers", () => {
  localStorage.setItem("datalab:panels:sql", '{"nav":"wide","chat":null}');
  render(<Harness />);
  expect(now(nav())).toBe(272);
  expect(now(chat())).toBe(416);
});

it("has no dividers on a narrow window: the list and the chat are drawers there", () => {
  wide(false);
  render(<Harness />);
  expect(screen.queryByRole("separator")).toBeNull();
  expect(screen.getByTestId("panels").style.gridTemplateColumns).toBe("minmax(0,1fr)");
});

it("has no chat divider while the chat is closed", () => {
  render(<Harness chatOpen={false} />);
  expect(nav()).toBeInTheDocument();
  expect(screen.queryByRole("separator", { name: "Resize the chat" })).toBeNull();
  expect(screen.getByTestId("panels").style.gridTemplateColumns).toBe("272px minmax(0,1fr)");
});
