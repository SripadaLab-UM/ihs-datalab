import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { knowledgeApi, type KnowledgeStatus, type SignIn } from "@/api/knowledge";

import { GitHubSection } from "./GitHubSection";

vi.mock("@/api/client", () => ({ api: { health: vi.fn() } }));
vi.mock("@/api/knowledge", async (original) => ({
  ...(await original<typeof import("@/api/knowledge")>()),
  knowledgeApi: {
    status: vi.fn(), sync: vi.fn(), signIn: vi.fn(), startSignIn: vi.fn(), pollSignIn: vi.fn(),
    cancelSignIn: vi.fn(), signOut: vi.fn(),
  },
})); // prettier-ignore

const status = (more: Partial<KnowledgeStatus> = {}): KnowledgeStatus => ({
  available: true, repo: "signed out", name: "SripadaLab-UM/ihs-knowledge", signed_in: false, account: null,
  head: null, last_sync: null, last_error: null, ahead: 0, behind: 0, message: "Sign in to GitHub to use it.", ...more,
}); // prettier-ignore
const signedIn = status({
  repo: "in sync", signed_in: true, account: { login: "yfang", name: "Yu Fang" }, message: null,
  head: "abc1234", last_sync: new Date(Date.now() - 5 * 60_000).toISOString(),
}); // prettier-ignore
const waiting: SignIn = {
  state: "waiting", user_code: "WDJB-MJHT", verification_uri: "https://github.com/login/device",
  expires_at: new Date(Date.now() + 900_000).toISOString(), interval: 5, account: null, message: null,
}; // prettier-ignore
const out: SignIn = { state: "signed out", user_code: null, verification_uri: null, expires_at: null, interval: null, account: null, message: null };

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GitHubSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.health).mockReset().mockResolvedValue({ profile: "real", version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 1 });
  for (const call of Object.values(knowledgeApi)) vi.mocked(call).mockReset();
  vi.mocked(knowledgeApi.status).mockResolvedValue(status());
  vi.mocked(knowledgeApi.signIn).mockResolvedValue(out);
  vi.mocked(knowledgeApi.sync).mockResolvedValue(signedIn);
});

afterEach(() => vi.useRealTimers());

it("practice never signs in to GitHub", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice", version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 1 });
  show();
  expect(await screen.findByText(/Practice DataLab never signs in to GitHub/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
  expect(knowledgeApi.status).not.toHaveBeenCalled();
});

it("says when the repositories aren't set up, and whom to ask", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue(
    status({ available: false, repo: "not configured", message: "The knowledge base's repository isn't set in settings.toml." }),
  );
  show();
  expect(await screen.findByText(/isn't set in settings.toml/)).toBeTruthy();
  expect(screen.getByText(/Ask the DataLab maintainer/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
});

it("signs in with the device flow: the code, GitHub's page, polling, then a sync", async () => {
  vi.mocked(knowledgeApi.startSignIn).mockResolvedValue(waiting);
  vi.mocked(knowledgeApi.pollSignIn)
    .mockResolvedValueOnce(waiting)
    .mockResolvedValueOnce({ ...out, state: "signed in", account: { login: "yfang", name: "Yu Fang" } });
  show();
  const start = await screen.findByRole("button", { name: "Sign in with GitHub" });
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fireEvent.click(start);
  expect(await screen.findByLabelText("Your code")).toHaveProperty("textContent", "WDJB-MJHT");
  const link = screen.getByRole("link", { name: /github.com\/login\/device/ });
  expect(link.getAttribute("href")).toBe("https://github.com/login/device");
  expect(link.getAttribute("rel")).toContain("noopener");
  expect(knowledgeApi.pollSignIn).not.toHaveBeenCalled();
  vi.mocked(knowledgeApi.status).mockResolvedValue(signedIn);
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(knowledgeApi.pollSignIn).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(knowledgeApi.pollSignIn).toHaveBeenCalledTimes(2);
  await waitFor(() => expect(knowledgeApi.sync).toHaveBeenCalled());
  expect(await screen.findByText("Yu Fang")).toBeTruthy();
  expect(screen.getByText("up to date")).toBeTruthy();
});

it("can cancel while waiting, and stops asking GitHub", async () => {
  vi.mocked(knowledgeApi.signIn).mockResolvedValue(waiting);
  vi.mocked(knowledgeApi.cancelSignIn).mockResolvedValue(out);
  show();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByRole("button", { name: "Sign in with GitHub" })).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(knowledgeApi.pollSignIn).not.toHaveBeenCalled();
});

it("says why when the code expired, and when the sign-in ran out", async () => {
  vi.mocked(knowledgeApi.signIn).mockResolvedValue({ ...out, state: "expired", message: "The code expired. Start again." });
  const view = show();
  expect(await screen.findByText("The code expired. Start again.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign in again" })).toBeTruthy();
  view.unmount();

  vi.mocked(knowledgeApi.signIn).mockResolvedValue({ ...out, message: "The GitHub sign-in has run out. Sign in again." });
  vi.mocked(knowledgeApi.status).mockResolvedValue(status({ message: "The GitHub sign-in has run out. Sign in again." }));
  show();
  expect(await screen.findByText("The GitHub sign-in has run out. Sign in again.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign in again" })).toBeTruthy();
});

it("shows who's signed in, the repo's state, and signs out", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue(signedIn);
  vi.mocked(knowledgeApi.signIn).mockResolvedValue({ ...out, state: "signed in", account: { login: "yfang", name: "Yu Fang" } });
  vi.mocked(knowledgeApi.signOut).mockResolvedValue(out);
  show();
  expect(await screen.findByText("Yu Fang")).toBeTruthy();
  expect(screen.getByText("@yfang")).toBeTruthy();
  expect(screen.getByText("synced 5 minutes ago")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Sync" }));
  await waitFor(() => expect(knowledgeApi.sync).toHaveBeenCalled());
  vi.mocked(knowledgeApi.status).mockResolvedValue(status());
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("button", { name: "Sign in with GitHub" })).toBeTruthy();
});

it("says whom to ask when GitHub won't let this account in", async () => {
  vi.mocked(knowledgeApi.status).mockResolvedValue({
    ...signedIn,
    repo: "no access",
    message: "GitHub says your GitHub account (@yfang) can't open SripadaLab-UM/ihs-knowledge. Ask Ali to add you to the datalab-users team in SripadaLab-UM, then press Sync.",
  });
  show();
  expect(await screen.findByText("no access")).toBeTruthy();
  expect(screen.getByText(/Ask Ali to add you to the datalab-users team/)).toBeTruthy();
});
