import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { type DockerStatus, dockerApi } from "@/api/docker";

import { DockerBanner } from "./DockerBanner";

vi.mock("@/api/docker", () => ({ dockerApi: { status: vi.fn(), check: vi.fn(), start: vi.fn(), fix: vi.fn() } }));

const refused: DockerStatus = { state: "vm-refused", fixing: false, admin_access_url: "https://admin.example.org/jit" };

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
  vi.mocked(dockerApi.check).mockReset();
  sessionStorage.clear();
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
  const link = within(dialog).getByRole("link", { name: "admin.example.org/jit" });
  expect(link).toHaveAttribute("href", "https://admin.example.org/jit");
  expect(link).toHaveAttribute("target", "_blank");
  expect(dialog).toHaveTextContent("Restarting Windows fixes it too");
  // Said before anything happens: the restart stops what runs in Docker.
  expect(dialog).toHaveTextContent("restarts Docker Desktop, so anything running in Docker stops");
  expect(dialog).toHaveTextContent("if it asks for a username and password, that's your own");
  expect(dockerApi.fix).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  expect(await within(dialog).findByRole("button", { name: "Waiting for Windows…" })).toBeDisabled();
  expect(dialog).toHaveTextContent("look for its box");
  answer({ outcome: "fixed", state: "ready", failed_step: null });
  expect(await within(dialog).findByText(/Fixed. Docker is running again/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  // Docker is ready, from the fix's answer: no banner.
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("a declined prompt says to check the admin access and try again; the banner reopens the dialog", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockResolvedValue({ outcome: "declined", state: "vm-refused", failed_step: null });
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
  expect(dialog).toHaveTextContent("on a Michigan Medicine computer, from your profile page");
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

it("the blocked banner is an alert; Later holds for the session, and the banner still reopens the dialog", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  const first = banner();
  const dialog = await screen.findByRole("dialog");
  expect(screen.getByRole("alert")).toHaveTextContent("Windows is blocking its virtual machine");
  fireEvent.click(within(dialog).getByRole("button", { name: "Later" }));
  first.unmount();
  // Another page load in the same session: no dialog by itself, but the banner offers it.
  banner();
  expect(await screen.findByRole("button", { name: "How to fix it…" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "How to fix it…" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});

it("check again asks for a new check, and a check that can't tell isn't called fixed", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.check).mockResolvedValue({ ...refused, state: "unknown" });
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Check again" }));
  expect(await within(dialog).findByText(/couldn't tell whether Windows lets it start/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Fix it" })).toBeEnabled();
  expect(within(dialog).queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
  expect(dockerApi.check).toHaveBeenCalledTimes(1);
});

it("says when Docker Desktop isn't installed", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue({ state: "not-installed", fixing: false, admin_access_url: null });
  banner();
  expect(await screen.findByRole("status")).toHaveTextContent("Run the DataLab installer again");
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("while Windows asks, the dialog says only that: not an older Check again result", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.check).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockReturnValue(new Promise(() => {}));
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Check again" }));
  expect(await within(dialog).findByText(/Still blocked/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  expect(await within(dialog).findByText(/Windows is asking for permission/)).toBeInTheDocument();
  expect(within(dialog).queryByText(/Still blocked/)).not.toBeInTheDocument();
});

it("names the step of the restart that didn't work, and says to restart Windows", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockResolvedValue({ outcome: "restart-failed", state: "starting", failed_step: "ready" });
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  const said = await within(dialog).findByText(/Windows lets Docker's virtual machine start again, but/);
  expect(said).toHaveTextContent("Docker Desktop didn't get ready within 4 minutes. Restart Windows to finish.");
  // The right is back, so Fix it would only say there's nothing to fix: it's gone.
  expect(within(dialog).queryByRole("button", { name: "Fix it" })).not.toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Done" })).toBeInTheDocument();
});

it("waits while something in DataLab is working, without asking Windows", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockResolvedValue({ outcome: "working", state: "vm-refused", failed_step: null });
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  expect(await within(dialog).findByText(/Something in DataLab is working/)).toHaveTextContent(
    "wait for it to finish, then click Fix it",
  );
  expect(within(dialog).getByRole("button", { name: "Fix it" })).toBeEnabled();
});

it("follows the fix's phase: waiting for Windows, then restarting Docker Desktop", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockReturnValue(new Promise(() => {}));
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  expect(await within(dialog).findByRole("button", { name: "Waiting for Windows…" })).toBeDisabled();
  // After Windows' Yes, DataLab says it's restarting (as after a reload mid-fix).
  vi.mocked(dockerApi.status).mockResolvedValue({ ...refused, state: "starting", fixing: true, phase: "restarting" });
  expect(await within(dialog).findByRole("button", { name: "Restarting Docker Desktop…" }, { timeout: 4000 })).toBeDisabled();
  expect(dialog).toHaveTextContent("Windows gave permission. DataLab is restarting Docker Desktop");
  expect(dialog).not.toHaveTextContent("Windows is asking for permission");
  // And the banner doesn't offer to open Docker Desktop in the middle of it.
  expect(screen.queryByRole("button", { name: "Open Docker Desktop" })).not.toBeInTheDocument();
});

it("nothing to fix doesn't read as if Docker works", async () => {
  vi.mocked(dockerApi.status).mockResolvedValue(refused);
  vi.mocked(dockerApi.fix).mockResolvedValue({ outcome: "not-needed", state: "starting", failed_step: null });
  banner();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Fix it" }));
  const said = await within(dialog).findByText(/nothing to fix here/);
  expect(said).toHaveTextContent("If Docker still doesn't start, restart Windows.");
  expect(said.className).not.toContain("text-data");
});
