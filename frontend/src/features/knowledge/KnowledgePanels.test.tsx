// The docked chat through resizing, closing and reopening, and the drawers on a narrow window.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { type ReactNode, useEffect } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { knowledgeApi } from "@/api/knowledge";

import { KnowledgePage } from "./KnowledgePage";

vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: { status: vi.fn(), sync: vi.fn(), pages: vi.fn(), page: vi.fn(), history: vi.fn() },
}));
const mounts = vi.hoisted(() => ({ count: 0 }));
vi.mock("@/components/chat/DockedChat", () => ({
  DockedChat: ({ headerActions }: { headerActions?: ReactNode }) => {
    useEffect(() => {
      mounts.count += 1;
    }, []);
    return (
      <div>
        <label>
          Draft <textarea defaultValue="" />
        </label>
        {headerActions}
      </div>
    );
  },
}));

const media = (matches: boolean) => {
  window.matchMedia = vi.fn(() => ({ matches, addEventListener() {}, removeEventListener() {} })) as never;
};

beforeEach(() => {
  mounts.count = 0;
  sessionStorage.clear();
  localStorage.clear();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1400 });
  vi.mocked(knowledgeApi.status).mockResolvedValue({
    available: true, repo: "in sync", name: "lab/kb", signed_in: true, account: null, head: "abc", last_sync: null,
    last_error: null, ahead: 0, behind: 0, message: null,
  }); // prettier-ignore
  vi.mocked(knowledgeApi.pages).mockResolvedValue({ head: "abc", pages: [] });
  vi.mocked(knowledgeApi.history).mockResolvedValue([]);
});

function show() {
  render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Routes>
          <Route path="knowledge/*" element={<KnowledgePage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

it("keeps the same chat, with its draft, through resizing, closing and reopening", async () => {
  media(true);
  show();
  const draft = await screen.findByRole("textbox", { name: "Draft" });
  fireEvent.change(draft, { target: { value: "half a question" } });
  const divider = screen.getByRole("separator", { name: "Resize the chat" });
  // Each divider names the panel it resizes.
  expect(document.getElementById(divider.getAttribute("aria-controls")!)).toBe(screen.getByRole("complementary", { name: "Knowledge chat" }));
  const list = screen.getByRole("separator", { name: "Resize the list" });
  expect(document.getElementById(list.getAttribute("aria-controls")!)).toBe(screen.getByRole("complementary", { name: "Pages and skills" }));
  fireEvent.keyDown(divider, { key: "ArrowLeft", shiftKey: true });
  fireEvent.keyDown(screen.getByRole("separator", { name: "Resize the list" }), { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("button", { name: "Hide the chat" }));
  expect(screen.queryByRole("separator", { name: "Resize the chat" })).toBeNull();
  expect(draft).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Ask for help/ }));
  expect(screen.getByRole("textbox", { name: "Draft" })).toBe(draft);
  expect(draft).toHaveValue("half a question");
  expect(mounts.count).toBe(1);
  // Collapsed state is remembered as before; widths per tab on this computer.
  expect(sessionStorage.getItem("datalab:kb:chat-open")).toBe("open");
  expect(JSON.parse(localStorage.getItem("datalab:panels:knowledge")!)).toEqual({ nav: 288, chat: 480 });
});

it("shows the list and the chat as drawers on a narrow window, with no dividers", async () => {
  media(false);
  show();
  await screen.findByRole("heading", { name: "Knowledge" });
  expect(screen.queryByRole("separator")).toBeNull();
  const list = screen.getByRole("complementary", { name: "Pages and skills" });
  expect(list.className).toMatch(/\bhidden\b/);
  fireEvent.click(screen.getByRole("button", { name: "Show pages and skills" }));
  expect(list.className).toMatch(/\babsolute\b/);
  expect(list).toHaveAttribute("role", "dialog");
  fireEvent.keyDown(list, { key: "Escape" });
  expect(list.className).toMatch(/\bhidden\b/);
  // The chat opens over the page and closes on Escape, and is the same chat when opened again.
  fireEvent.click(screen.getByRole("button", { name: /Ask for help/ }));
  const box = screen.getByRole("dialog", { name: "Knowledge chat" });
  expect(box.className).toMatch(/\babsolute\b/);
  fireEvent.keyDown(box, { key: "Escape" });
  expect(box).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Ask for help/ }));
  expect(mounts.count).toBe(1);
});
