import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { type GitHubStatus, githubApi, type SignIn } from "@/api/github";
import { knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";
import { pipelinesApi, type PipelinesStatus } from "@/api/pipelines";

import { GitHubSection } from "./GitHubSection";

vi.mock("@/api/client", () => ({ api: { health: vi.fn(), catalogStatus: vi.fn() } }));
vi.mock("@/api/github", () => ({
  githubApi: {
    status: vi.fn(), signIn: vi.fn(), startSignIn: vi.fn(), pollSignIn: vi.fn(), cancelSignIn: vi.fn(), signOut: vi.fn(),
  },
})); // prettier-ignore
vi.mock("@/api/knowledge", () => ({ knowledgeApi: { status: vi.fn(), sync: vi.fn() } }));
vi.mock("@/api/pipelines", () => ({ pipelinesApi: { status: vi.fn(), sync: vi.fn() } }));

const KB = { area: "knowledge" as const, name: "SripadaLab-UM/ihs-knowledge" };
const PIPES = { area: "pipelines" as const, name: "SripadaLab-UM/ihs-pipelines" };
const me = { login: "yfang", name: "Yu Fang" };
const github = (more: Partial<GitHubStatus> = {}): GitHubStatus => ({
  available: true, message: null, signed_in: false, account: null, repos: [KB], ...more,
}); // prettier-ignore
const status = (more: Partial<KnowledgeStatus> = {}): KnowledgeStatus => ({
  available: true, repo: "signed out", name: "SripadaLab-UM/ihs-knowledge", signed_in: false, account: null,
  head: null, last_sync: null, last_error: null, ahead: 0, behind: 0, message: "Sign in to GitHub to use it.", ...more,
}); // prettier-ignore
const signedIn = status({
  repo: "in sync", signed_in: true, account: me, message: null,
  head: "abc1234", last_sync: new Date(Date.now() - 5 * 60_000).toISOString(),
}); // prettier-ignore
const pipelinesSynced: PipelinesStatus = { ...signedIn, practice: false, name: "SripadaLab-UM/ihs-pipelines" };
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
  for (const call of [...Object.values(githubApi), ...Object.values(knowledgeApi), ...Object.values(pipelinesApi)]) {
    vi.mocked(call).mockReset();
  }
  vi.mocked(githubApi.status).mockResolvedValue(github());
  vi.mocked(githubApi.signIn).mockResolvedValue(out);
  vi.mocked(knowledgeApi.status).mockResolvedValue(status());
  vi.mocked(knowledgeApi.sync).mockResolvedValue(signedIn);
  vi.mocked(pipelinesApi.status).mockResolvedValue(pipelinesSynced);
  vi.mocked(pipelinesApi.sync).mockResolvedValue(pipelinesSynced);
});

afterEach(() => vi.useRealTimers());

it.each([
  ["knowledge", true],
  ["setting", false],
] as const)("with no catalog from %s, says the knowledge base's sync brings it: %s", async (source, says) => {
  vi.mocked(api.health).mockResolvedValue({ profile: "real", version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 0, catalog_state: "empty" });
  vi.mocked(api.catalogStatus).mockResolvedValue({ state: "empty", tables: 0, detail: "x", source });
  show();
  await screen.findByText(/Signed in|Sign in with GitHub/);
  await waitFor(() => expect(api.catalogStatus).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 0));
  expect(Boolean(screen.queryByText(/It comes from the knowledge base: sign in and sync it/))).toBe(says);
});

it("practice never signs in to GitHub", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice", version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 1 });
  show();
  expect(await screen.findByText(/Practice DataLab never signs in to GitHub/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
  expect(githubApi.status).not.toHaveBeenCalled();
  // Labelled, not missing.
  expect(screen.getByText("Not used on the practice DataLab")).toBeTruthy();
});

it("switches account: signs out, then asks GitHub for a new code", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(github({ signed_in: true, account: me }));
  vi.mocked(knowledgeApi.status).mockResolvedValue(signedIn);
  vi.mocked(githubApi.signIn).mockResolvedValue({ ...out, state: "signed in", account: me });
  vi.mocked(githubApi.signOut).mockResolvedValue(out);
  vi.mocked(githubApi.startSignIn).mockResolvedValue(waiting);
  show();
  expect(await screen.findByText("Signed in")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Switch account…" }));
  expect(await screen.findByLabelText("Your code")).toHaveProperty("textContent", "WDJB-MJHT");
  expect(githubApi.signOut).toHaveBeenCalledBefore(vi.mocked(githubApi.startSignIn));
  expect(screen.getByText("Waiting for the code")).toBeTruthy();
});

it("says when the repositories aren't set up, and whom to ask", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(
    github({ available: false, repos: [], message: "Neither of the lab's repositories is set in settings.toml ([repos])." }),
  );
  show();
  expect(await screen.findByText(/is set in settings.toml/)).toBeTruthy();
  expect(screen.getByText(/Ask the DataLab maintainer/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
});

it("signs in with the device flow: the code, GitHub's page, polling, then a sync of each repo", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(github({ repos: [KB, PIPES] }));
  vi.mocked(githubApi.startSignIn).mockResolvedValue(waiting);
  vi.mocked(githubApi.pollSignIn)
    .mockResolvedValueOnce(waiting)
    .mockResolvedValueOnce({ ...out, state: "signed in", account: me });
  show();
  const start = await screen.findByRole("button", { name: "Sign in with GitHub" });
  expect(screen.getAllByText(/knowledge base and pipelines repo/).length).toBeGreaterThan(0);
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fireEvent.click(start);
  expect(await screen.findByLabelText("Your code")).toHaveProperty("textContent", "WDJB-MJHT");
  const link = screen.getByRole("link", { name: /github.com\/login\/device/ });
  expect(link.getAttribute("href")).toBe("https://github.com/login/device");
  expect(link.getAttribute("rel")).toContain("noopener");
  expect(githubApi.pollSignIn).not.toHaveBeenCalled();
  vi.mocked(githubApi.status).mockResolvedValue(github({ repos: [KB, PIPES], signed_in: true, account: me }));
  vi.mocked(knowledgeApi.status).mockResolvedValue(signedIn);
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(githubApi.pollSignIn).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(githubApi.pollSignIn).toHaveBeenCalledTimes(2);
  await waitFor(() => expect(knowledgeApi.sync).toHaveBeenCalled());
  expect(pipelinesApi.sync).toHaveBeenCalled();
  expect(await screen.findByText("Yu Fang")).toBeTruthy();
  expect(screen.getAllByText("up to date")).toHaveLength(2);
});

it("signs in with only the pipelines repo set up, and shows only that repo", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(github({ repos: [PIPES] }));
  const view = show();
  expect(await screen.findByRole("button", { name: "Sign in with GitHub" })).toBeTruthy();
  expect(screen.getByText(/the lab's pipelines repo \(/)).toBeTruthy();
  view.unmount();
  vi.mocked(githubApi.status).mockResolvedValue(github({ repos: [PIPES], signed_in: true, account: me }));
  show();
  expect(await screen.findByText("SripadaLab-UM/ihs-pipelines")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Sync the pipelines repo" }));
  await waitFor(() => expect(pipelinesApi.sync).toHaveBeenCalled());
  expect(knowledgeApi.status).not.toHaveBeenCalled();
});

it("can cancel while waiting, and stops asking GitHub", async () => {
  vi.mocked(githubApi.signIn).mockResolvedValue(waiting);
  vi.mocked(githubApi.cancelSignIn).mockResolvedValue(out);
  show();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByRole("button", { name: "Sign in with GitHub" })).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(githubApi.pollSignIn).not.toHaveBeenCalled();
});

it("says why when the code expired, and when the sign-in ran out", async () => {
  vi.mocked(githubApi.signIn).mockResolvedValue({ ...out, state: "expired", message: "The code expired. Start again." });
  const view = show();
  expect(await screen.findByText("The code expired. Start again.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign in again" })).toBeTruthy();
  view.unmount();

  vi.mocked(githubApi.signIn).mockResolvedValue({ ...out, message: "The GitHub sign-in has run out. Sign in again." });
  show();
  expect(await screen.findByText("The GitHub sign-in has run out. Sign in again.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign in again" })).toBeTruthy();
});

it("shows who's signed in, the repo's state, and signs out", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(github({ signed_in: true, account: me }));
  vi.mocked(knowledgeApi.status).mockResolvedValue(signedIn);
  vi.mocked(githubApi.signIn).mockResolvedValue({ ...out, state: "signed in", account: me });
  vi.mocked(githubApi.signOut).mockResolvedValue(out);
  show();
  expect(await screen.findByText("Yu Fang")).toBeTruthy();
  expect(screen.getByText("@yfang")).toBeTruthy();
  expect(await screen.findByText("synced 5 minutes ago")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Sync the knowledge base" }));
  await waitFor(() => expect(knowledgeApi.sync).toHaveBeenCalled());
  vi.mocked(githubApi.status).mockResolvedValue(github());
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("button", { name: "Sign in with GitHub" })).toBeTruthy();
});

it("says whom to ask when GitHub won't let this account in", async () => {
  vi.mocked(githubApi.status).mockResolvedValue(github({ signed_in: true, account: me }));
  vi.mocked(knowledgeApi.status).mockResolvedValue({
    ...signedIn,
    repo: "no access",
    message: "GitHub says your GitHub account (@yfang) can't open SripadaLab-UM/ihs-knowledge. Ask Ali to add you to the datalab-users team in SripadaLab-UM, then press Sync.",
  });
  show();
  expect(await screen.findByText("no access")).toBeTruthy();
  expect(screen.getByText(/Ask Ali to add you to the datalab-users team/)).toBeTruthy();
});

it("stops asking GitHub once the page is closed mid-sign-in", async () => {
  vi.mocked(githubApi.signIn).mockResolvedValue(waiting);
  vi.mocked(githubApi.pollSignIn).mockResolvedValue(waiting);
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const view = show();
  await screen.findByLabelText("Your code");
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(githubApi.pollSignIn).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(githubApi.pollSignIn).toHaveBeenCalledTimes(1);
  expect(githubApi.cancelSignIn).not.toHaveBeenCalled(); // the code still works if they come back
  expect(errors).not.toHaveBeenCalled();
  errors.mockRestore();
});

it("a poll still out when the page closes changes nothing", async () => {
  vi.mocked(githubApi.signIn).mockResolvedValue(waiting);
  let answer: (value: SignIn) => void = () => {};
  vi.mocked(githubApi.pollSignIn).mockImplementation(() => new Promise((resolve) => (answer = resolve)));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const view = show();
  await screen.findByLabelText("Your code");
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  view.unmount();
  await act(async () => answer({ ...out, state: "signed in", account: me }));
  expect(knowledgeApi.sync).not.toHaveBeenCalled();
});
