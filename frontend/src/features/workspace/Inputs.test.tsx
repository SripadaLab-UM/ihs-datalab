import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, type Conversation } from "@/api/client";

import { Inputs } from "./Inputs";

vi.mock("@/api/client", () => ({
  api: { inputs: vi.fn(), modes: vi.fn(), inputSamples: vi.fn(async () => []), attach: vi.fn() },
}));

const conversation = (mode: string) => ({ id: "c1", kind: "data", mode, title: "t", model: "m", busy: false }) as Conversation;

beforeEach(() => {
  vi.mocked(api.inputs).mockResolvedValue([]);
  vi.mocked(api.modes).mockResolvedValue([
    { id: "knowledge", label: "Knowledge writing", kind: "data", description: "", starters: [], tab_only: true, queries: false, attachments: false, question: "?", remember: false },
    { id: "workflows", label: "Workflow authoring", kind: "data", description: "", starters: [], tab_only: true, queries: true, attachments: false, question: "?", remember: false },
    { id: "extraction", label: "Data extraction", kind: "data", description: "", starters: [], tab_only: false, queries: true, attachments: true, question: "?", remember: true },
  ]); // prettier-ignore
});

function show(mode: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Inputs conversation={conversation(mode)} practice={false} />
    </QueryClientProvider>,
  );
}

it("offers nothing to attach in a mode that takes no attachments", async () => {
  show("knowledge");
  expect(await screen.findByText("Nothing can be attached here")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Attach/ })).toBeNull();
});

it("says why a tab's chat that queries takes no attachments", async () => {
  show("workflows");
  expect(await screen.findByText(/Workflow authoring works from the database and its tab's own files/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Attach/ })).toBeNull();
});

it("offers files and folders in the other modes", async () => {
  show("extraction");
  expect(await screen.findByRole("button", { name: /Attach files/ })).toBeInTheDocument();
});

it("while the picker is open, says it may be behind this window", async () => {
  vi.mocked(api.attach).mockReturnValue(new Promise(() => {}));
  show("extraction");
  fireEvent.click(await screen.findByRole("button", { name: /Attach folder/ }));
  expect((await screen.findByRole("status")).textContent).toMatch(/^A folder picker is open.*may be behind this window/);
});
