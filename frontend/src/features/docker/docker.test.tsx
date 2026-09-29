import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { type DockerStatus, dockerApi } from "@/api/docker";

import { DockerBanner } from "./DockerBanner";

vi.mock("@/api/docker", () => ({ dockerApi: { status: vi.fn(), start: vi.fn(), fix: vi.fn() } }));

const refused: DockerStatus = { state: "vm-refused", fixing: false, admin_access_url: "https://profile.med.umich.edu" };

function banner() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DockerBanner />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(dockerApi.status).mockReset();
  vi.mocked(dockerApi.start).mockReset();
  vi.mocked(dockerApi.fix).mockReset();
});

it("shows nothing while Docker is ready, or off Windows", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue({ state: "ready", fixing: false, admin_access_url: null });
  const { container } = banner();
  await waitFor(() => expect(dockerApi.status).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

it("walks through turning on admin access, then asks Windows only when Fix it is pressed", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  let answer: (value: Awaited<ReturnType<typeof dockerApi.fix>>) => void = () => {};
  vi.mocked(dockerApi.fix).mockReturnValue(new Promise((resolve) => (answer = resolve)));
  banner();
  // The dialog opens by itself the first time.
  const dialog = await screen.findByRole("dialog", { name: "Docker needs a Windows fix" });
  const link = within(dialog).getByRole("link", { name: "profile.med.umich.edu" });
  expect(link).toHaveAttribute("href", "https://profile.med.umich.edu");
  expect(link).toHaveAttribute("target", "_blank");
  expect(dialog).toHaveTextContent("Restarting Windows fixes it too");
  expect(dockerApi.fix).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  expect(await within(dialog).findByRole("button", { name: "Waiting for Windows…" })).toBeDisabled();
  expect(dialog).toHaveTextContent("look for its box");
  answer({ outcome: "fixed", state: "starting" });
  expect(await within(dialog).findByText(/Fixed\. Docker Desktop is restarting/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  // The banner now says Docker is starting, from the fix's answer.
  expect(screen.getByRole("status")).toHaveTextContent("Docker Desktop is starting");
});

it("a declined prompt says to check the admin access and try again; the banner reopens the dialog", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockResolvedValue({ outcome: "declined", state: "vm-refused" });
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  expect(await within(dialog).findByText(/Windows didn't give permission/)).toHaveTextContent(
    "temporary administrator access is on",
  );
  expect(within(dialog).getByRole("button", { name: "Fix it" })).toBeEnabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Later" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  // Later, not never: the banner stays and opens it again.
  fireEvent.click(screen.getByRole("button", { name: "How to fix it…" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});

it("another lab with no admin page says to ask IT", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue({ ...refused, admin_access_url: null });
  banner();
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("ask IT if you don't have it");
  expect(within(dialog).queryByRole("link")).not.toBeInTheDocument();
});

it("offers to open a closed Docker Desktop", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue({ state: "stopped", fixing: false, admin_access_url: null });
  vi.mocked(dockerApi.start).mockResolvedValue({ state: "starting", fixing: false, admin_access_url: null });
  banner();
  fireEvent.click(await screen.findByRole("button", { name: "Open Docker Desktop" }));
  expect(await screen.findByText(/Docker Desktop is starting/)).toBeInTheDocument();
  expect(dockerApi.fix).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
