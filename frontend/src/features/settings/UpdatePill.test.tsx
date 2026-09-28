import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

import { settingsApi, type UpdateCheck } from "@/api/settings";

import { UpdatePill, UpdatingBanner } from "./UpdatePill";

vi.mock("@/api/settings", async (original) => ({
  ...(await original<typeof import("@/api/settings")>()),
  settingsApi: { updateCheck: vi.fn() },
}));

const mocked = vi.mocked(settingsApi);

const CHECK: UpdateCheck = {
  state: "up-to-date",
  message: "DataLab 0.1.0a2 is the newest release or pre-release.",
  current_version: "0.1.0a2",
  channel: "auto",
  checked_at: "2026-09-27T10:00:00+00:00",
  available: null,
  can_install: false,
  cannot_install_because: null,
  install: { state: "idle", version: null, message: "", started_at: null, updated_at: null },
  updating: false,
};

const RELEASE = {
  version: "0.1.0a3",
  tag: "v0.1.0-alpha.3",
  title: "DataLab v0.1.0-alpha.3",
  notes: "",
  published_at: null,
  page: null,
  prerelease: true,
  size_bytes: 1,
};

function show() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <UpdatePill />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => mocked.updateCheck.mockReset());

it("shows Update available, linking to Settings → Updates", async () => {
  mocked.updateCheck.mockResolvedValue({ ...CHECK, state: "available", available: RELEASE, can_install: true });
  show();
  const pill = await screen.findByRole("link", { name: "Update available" });
  expect(pill.getAttribute("href")).toBe("/settings/updates#updates-check");
  expect(pill.getAttribute("title")).toContain("DataLab 0.1.0a3 is available (this is 0.1.0a2)");
});

it.each(["up-to-date", "offline", "rate-limited", "not-visible", "failed", "not-checked", "not-configured"] as const)(
  "shows nothing when the check says %s",
  async (state) => {
    mocked.updateCheck.mockResolvedValue({ ...CHECK, state });
    const { container } = show();
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mocked.updateCheck).toHaveBeenCalled();
    expect(container.textContent).toBe("");
  },
);

it("says an update is being installed", async () => {
  mocked.updateCheck.mockResolvedValue({
    ...CHECK,
    state: "available",
    available: RELEASE,
    install: { state: "installing", version: "0.1.0a3", message: "", started_at: null, updated_at: null },
  });
  show();
  expect(await screen.findByText("Updating to 0.1.0a3…")).toBeTruthy();
});

it("says on every page that nothing new can start while it updates", async () => {
  mocked.updateCheck.mockResolvedValue({
    ...CHECK,
    updating: true,
    install: { state: "backing-up", version: "0.1.0a3", message: "", started_at: null, updated_at: null },
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <UpdatingBanner />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/being updated to 0.1.0a3. Nothing new can start/)).toBeTruthy();
});

it("shows no banner otherwise", async () => {
  mocked.updateCheck.mockResolvedValue(CHECK);
  const { container } = render(
    <QueryClientProvider client={new QueryClient()}>
      <UpdatingBanner />
    </QueryClientProvider>,
  );
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(container.textContent).toBe("");
});
