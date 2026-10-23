import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, ApiError, type Conversation } from "@/api/client";

import { Inputs } from "./Inputs";

vi.mock("@/api/client", () => ({
  api: {
    inputs: vi.fn(),
    modes: vi.fn(),
    inputSamples: vi.fn(async () => []),
    attach: vi.fn(),
    health: vi.fn(),
    startUpload: vi.fn(),
    uploadFile: vi.fn(),
    attachUpload: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      message: string,
    ) {
      super(message);
    }
  },
}));

const conversation = (mode: string) => ({ id: "c1", kind: "data", mode, title: "t", model: "m", busy: false }) as Conversation;

beforeEach(() => {
  vi.mocked(api.health).mockResolvedValue({ deployment: "desktop" } as Awaited<ReturnType<typeof api.health>>);
  vi.mocked(api.inputs).mockResolvedValue([]);
  vi.mocked(api.modes).mockResolvedValue([
    { id: "knowledge", label: "Knowledge writing", kind: "data", description: "", starters: [], tab_only: true, queries: false, attachments: false, question: "?", remember: false, sources: ["database"] },
    { id: "workflows", label: "Workflow authoring", kind: "data", description: "", starters: [], tab_only: true, queries: true, attachments: false, question: "?", remember: false, sources: ["database"] },
    { id: "extraction", label: "Data extraction", kind: "data", description: "", starters: [], tab_only: false, queries: true, attachments: true, question: "?", remember: true, sources: ["database"] },
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

it("on the lab's server, uploads what's chosen in the browser and attaches it", async () => {
  vi.mocked(api.health).mockResolvedValue({ deployment: "server" } as Awaited<ReturnType<typeof api.health>>);
  vi.mocked(api.startUpload).mockResolvedValue({ id: "up_1", kind: "folder", max_file_bytes: 100, room_bytes: 1000 });
  vi.mocked(api.uploadFile).mockImplementation(async (_id, _upload, path) => {
    if (path.endsWith(".netrc")) throw new ApiError(422, ".netrc looks like a credentials file, so it wasn't uploaded.");
    return { path, bytes: 1 };
  });
  vi.mocked(api.attachUpload).mockResolvedValue({ added: [], refused: [], warnings: [] });
  show("extraction");
  expect(await screen.findByRole("button", { name: /Upload folder/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Attach files/ })).toBeNull(); // no picker on a server
  const file = (path: string) => {
    const made = new File(["x"], path.split("/").at(-1)!);
    Object.defineProperty(made, "webkitRelativePath", { value: path });
    return made;
  };
  fireEvent.change(screen.getByTestId("upload-folder"), {
    target: { files: [file("survey/a.csv"), file("survey/.netrc")] },
  });
  await waitFor(() => expect(api.attachUpload).toHaveBeenCalledWith("c1", "up_1"));
  expect(vi.mocked(api.uploadFile).mock.calls.map((call) => call[2])).toEqual(["survey/a.csv", "survey/.netrc"]);
  expect(await screen.findByText(/Not attached: .netrc looks like a credentials file/)).toBeInTheDocument();
});
