import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type Health } from "@/api/client";
import { githubApi } from "@/api/github";
import { sessionApi } from "@/api/session";
import { type Connections, settingsApi } from "@/api/settings";

import { DatabaseShortcut, databaseStanding, KeyShortcut, whilePracticeDatabaseStarts } from "./ConnectionShortcuts";
import { AttentionMenu } from "./AttentionMenu";
import { FoldersShortcut } from "./FoldersShortcut";
import { MoreMenu } from "./MoreMenu";
import { activityWarning, SessionMenu } from "./SessionMenu";

vi.mock("@/api/client", () => ({ api: { health: vi.fn(), destinations: vi.fn() } }));
vi.mock("@/api/github", () => ({ githubApi: { status: vi.fn() } }));
vi.mock("@/api/session", () => ({ sessionApi: { end: vi.fn(), activity: vi.fn() } }));
vi.mock("@/features/settings/updateCheck", () => ({ useUpdateCheck: vi.fn(() => ({ data: undefined })) }));
vi.mock("@/api/settings", () => ({
  settingsApi: {
    connections: vi.fn(),
    testConnections: vi.fn(),
    feedbackContact: vi.fn(),
    diagnostics: vi.fn(),
    updateCheck: vi.fn(),
  },
}));
vi.mock("@/components/chat/plan", () => ({ clearAllDrafts: vi.fn() }));

const SECRET = "sk-THIS-IS-THE-KEY-1234";

function connections(more: { practice?: boolean; key?: string; password?: string | null; configured?: boolean } = {}) {
  const practice = more.practice ?? false;
  return {
    profile: practice ? "practice" : "real",
    settings_file: "/x/settings.toml",
    oracle: {
      configured: more.configured ?? true,
      practice,
      dsn: practice ? "localhost:1521/FREEPDB1" : "db.example.edu:1521/IHS",
      user: "IHS_READER",
      read_only_roles: ["IHS_RO"],
      allowed_schemas: ["IHS_2025"],
      password: more.password === undefined ? (practice ? null : "keychain") : more.password,
      can_set_password: !practice,
    },
    model: { base_url: "https://umgpt.example/v1", key: more.key ?? "keychain", can_set_key: !practice },
    read_only_because: null,
  } as unknown as Connections;
}

function Probe() {
  const location = useLocation();
  return (
    <p data-testid="at">
      {location.pathname}
      {location.hash} {JSON.stringify(location.state)}
    </p>
  );
}

function show(ui: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/workspace"]}>
        {ui}
        <Routes>
          <Route path="*" element={<Probe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.health).mockReset().mockResolvedValue({ profile: "real", version: "0.2.0" } as never);
  vi.mocked(api.destinations).mockReset().mockResolvedValue([]);
  vi.mocked(githubApi.status).mockReset().mockResolvedValue({ available: false } as never);
  vi.mocked(settingsApi.connections).mockReset().mockResolvedValue(connections());
  vi.mocked(settingsApi.testConnections).mockReset();
  vi.mocked(settingsApi.feedbackContact).mockReset().mockResolvedValue({ contact: null, email: null });
  vi.mocked(settingsApi.diagnostics).mockReset().mockResolvedValue({ text: "DataLab 0.2.0\nSafety: ok" });
  vi.mocked(sessionApi.end).mockReset().mockResolvedValue(undefined);
  vi.mocked(sessionApi.activity).mockReset().mockResolvedValue({ agent_turn: false, workflow_run: false });
});
afterEach(() => vi.restoreAllMocks());

// ------------------------------------------------------------ database

it("database: set up, untested, is quiet; the tooltip names the state and what a click does", async () => {
  show(<DatabaseShortcut />);
  const button = await screen.findByRole("button", { name: "Database: set up, not tested yet" });
  expect(button).toHaveAttribute("title", "Database: set up, not tested yet · click to test or open settings");
  expect(button).toHaveAttribute("aria-haspopup", "dialog");
  expect(button).toHaveAttribute("aria-expanded", "false");
  expect(button).toHaveTextContent(""); // an icon only: nothing needs attention
});

it("database: says in words when it needs attention", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ password: "missing" }));
  show(<DatabaseShortcut />);
  expect(await screen.findByRole("button", { name: "Database: password missing" })).toHaveTextContent("DB: no password");
});

it("database: not set up", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ configured: false, password: null }));
  show(<DatabaseShortcut />);
  const button = await screen.findByRole("button", { name: "Database: not set up" });
  expect(button).toHaveTextContent("DB: not set up");
  fireEvent.click(button);
  expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
});

it("database: Test connection calls the existing test and shows the result; a failure shows on the button", async () => {
  vi.mocked(settingsApi.testConnections).mockResolvedValue({
    database: { ok: false, message: "Can't reach the database.", enabled_roles: [], read_only: null },
    model: { ok: true, message: "ok" },
  });
  show(<DatabaseShortcut />);
  fireEvent.click(await screen.findByRole("button", { name: "Database: set up, not tested yet" }));
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByText("Can't reach the database.")).toBeInTheDocument();
  expect(settingsApi.testConnections).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Database: test failed" })).toHaveTextContent("DB: not connected");
});

it("database: Open connection settings links to Connections, asking to highlight the database", async () => {
  show(<DatabaseShortcut />);
  fireEvent.click(await screen.findByRole("button", { name: /^Database/ }));
  fireEvent.click(screen.getByRole("link", { name: "Open connection settings" }));
  expect(screen.getByTestId("at")).toHaveTextContent(
    '/settings/connections#connection-database {"highlight":"connection-database"}',
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("database: on practice, the synthetic database, fixed", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ practice: true }));
  show(<DatabaseShortcut />);
  const button = await screen.findByRole("button", { name: "Database: synthetic, not tested yet" });
  fireEvent.click(button);
  expect(screen.getByText("Fixed on the practice DataLab: the synthetic database")).toBeInTheDocument();
});

// ------------------------------------------------------------ U-M GPT key

it("key: saved is quiet, and the panel never shows the key, only where it is", async () => {
  show(<KeyShortcut />);
  const button = await screen.findByRole("button", { name: "U-M GPT key: saved in keychain" });
  fireEvent.click(button);
  const panel = screen.getByRole("dialog");
  expect(panel).toHaveTextContent("Saved in keychain");
  expect(within(panel).getByRole("link", { name: "Replace key…" })).toHaveAttribute(
    "href",
    "/settings/connections#connection-umgpt",
  );
  expect(document.body.textContent).not.toContain(SECRET);
  expect(document.body.innerHTML).not.toMatch(/sk-|•••|\*\*\*/);
});

it("key: missing says so in words, and offers Add key", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ key: "missing" }));
  show(<KeyShortcut />);
  const button = await screen.findByRole("button", { name: "U-M GPT key: not saved" });
  expect(button).toHaveTextContent("Key missing");
  fireEvent.click(button);
  fireEvent.click(screen.getByRole("link", { name: "Add key…" }));
  expect(screen.getByTestId("at")).toHaveTextContent('{"highlight":"connection-umgpt"}');
});

it("key: Test connection shows the model's result", async () => {
  vi.mocked(settingsApi.testConnections).mockResolvedValue({
    database: { ok: true, message: "ok", enabled_roles: [], read_only: true },
    model: { ok: false, message: "U-M GPT refused the key." },
  });
  show(<KeyShortcut />);
  fireEvent.click(await screen.findByRole("button", { name: "U-M GPT key: saved in keychain" }));
  fireEvent.click(screen.getByRole("button", { name: "Test both connections" }));
  expect(await screen.findByText("U-M GPT refused the key.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "U-M GPT key: test failed" })).toHaveTextContent("Key: not working");
});

it("key: on practice, it can't be set here", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ practice: true }));
  show(<KeyShortcut />);
  fireEvent.click(await screen.findByRole("button", { name: /^U-M GPT key/ }));
  expect(screen.queryByRole("link", { name: /key…/ })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open connection settings" })).toBeInTheDocument();
});

// ------------------------------------------------------------ folders

const folder = (more: object = {}) => ({
  id: "a",
  name: "Lab Dropbox",
  path: "/Users/x/Dropbox/IHS",
  where: "~/Dropbox/IHS",
  available: true,
  status: "ready",
  status_message: null,
  warning: null,
  location: "sync_folder",
  sync_provider: "dropbox",
  sync_provider_name: "Dropbox",
  location_note: "",
  offered: true,
  workflow_keys: [],
  practice: false,
  ...more,
});

it("folders: lists each with whether it's available, and links to Manage folders", async () => {
  vi.mocked(api.destinations).mockResolvedValue([
    folder(),
    folder({ id: "b", name: "USB", available: false, status: "missing", status_message: "Is the drive plugged in?" }),
  ] as never);
  show(<FoldersShortcut />);
  const button = await screen.findByRole("button", { name: "Export folders: 1 of 2 unavailable" });
  expect(button).toHaveTextContent("Folder unavailable");
  fireEvent.click(button);
  expect(screen.getByText("Lab Dropbox")).toBeInTheDocument();
  expect(screen.getByText("Is the drive plugged in?")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Manage folders" })).toHaveAttribute("href", "/settings/export-folders#export-folders");
});

it("folders: on practice, only its own folder", async () => {
  vi.mocked(api.health).mockResolvedValue({ profile: "practice" } as never);
  vi.mocked(api.destinations).mockResolvedValue([
    folder({ id: "p", name: "Practice exports", practice: true }),
    folder({ id: "x", name: "Someone's Dropbox" }),
  ] as never);
  show(<FoldersShortcut />);
  fireEvent.click(await screen.findByRole("button", { name: "Export folders: 1 folder, available" }));
  expect(screen.getByText("Practice exports")).toBeInTheDocument();
  expect(screen.queryByText("Someone's Dropbox")).not.toBeInTheDocument();
});

// ------------------------------------------------------------ keyboard

it("a popover opens from the keyboard, takes focus, and Escape closes it and gives focus back", async () => {
  show(<DatabaseShortcut />);
  const button = await screen.findByRole("button", { name: /^Database/ });
  button.focus();
  fireEvent.click(button); // Enter or Space on a button is a click
  expect(button).toHaveAttribute("aria-expanded", "true");
  const panel = screen.getByRole("dialog");
  expect(button).toHaveAttribute("aria-controls", panel.id);
  expect(panel).toContainElement(document.activeElement as HTMLElement);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(button).toHaveFocus();
});

it("More: arrow keys move through the items, Escape closes it", async () => {
  show(<MoreMenu />);
  const button = screen.getByRole("button", { name: "More" });
  expect(button).toHaveAttribute("aria-haspopup", "menu");
  fireEvent.keyDown(button, { key: "ArrowDown" });
  const menu = screen.getByRole("menu");
  const items = [...menu.querySelectorAll<HTMLElement>('[role^="menuitem"]')];
  expect(items[0]).toHaveFocus();
  fireEvent.keyDown(items[0], { key: "ArrowDown" });
  expect(items[1]).toHaveFocus();
  fireEvent.keyDown(items[1], { key: "End" });
  expect(items.at(-1)).toHaveFocus();
  fireEvent.keyDown(items.at(-1)!, { key: "ArrowDown" });
  expect(items[0]).toHaveFocus();
  fireEvent.keyDown(items[0], { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(button).toHaveFocus();
});

// ------------------------------------------------------------ More

it("More: Appearance is a Light / Dark / System choice that applies at once", () => {
  show(<MoreMenu />);
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  const group = screen.getByRole("group", { name: "Appearance" });
  const radios = within(group).getAllByRole("menuitemradio");
  expect(radios.map((r) => r.textContent)).toEqual(["Light", "Dark", "System"]);
  expect(within(group).getByRole("menuitemradio", { name: "System" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(within(group).getByRole("menuitemradio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(within(group).getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(within(group).getByRole("menuitemradio", { name: "System" }));
  expect(document.documentElement.dataset.theme).toBeUndefined();
});

it("More: on a narrow window it holds Export folders and GitHub, hidden from 2xl up", async () => {
  vi.mocked(githubApi.status).mockResolvedValue({
    available: true,
    signed_in: true,
    account: { login: "yfang", name: null },
    message: null,
    repos: [],
  } as never);
  show(<MoreMenu />);
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  const folders = screen.getByRole("menuitem", { name: "Export folders…" });
  expect(folders.parentElement).toHaveClass("2xl:hidden");
  const github = await screen.findByRole("menuitem", { name: /GitHub: yfang/ });
  expect(github).toHaveAttribute("href", "/settings/connections#connection-github");
  expect(github.parentElement).toHaveClass("2xl:hidden");
  // Feedback and Appearance show at every width.
  expect(screen.getByRole("menuitem", { name: "Send feedback…" }).closest(".\\32xl\\:hidden")).toBeNull();
  // End session isn't here: it has its own menu.
  expect(screen.queryByText(/End session/)).not.toBeInTheDocument();
  fireEvent.click(folders);
  expect(await screen.findByRole("dialog", { name: "Export folders" })).toBeInTheDocument();
});

// ------------------------------------------------------------ End session

it("End session needs confirming, says a restart is needed, and only then ends it", async () => {
  const ended = vi.fn();
  show(<SessionMenu onEnded={ended} />);
  fireEvent.click(screen.getByRole("button", { name: "Session" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "End session…" }));
  const dialog = screen.getByRole("dialog", { name: "End this session?" });
  expect(dialog).toHaveTextContent("You'll need a new sign-in link from the launcher.");
  expect(dialog).toHaveTextContent(
    "quit DataLab (close its window, Terminal on a Mac or PowerShell on Windows, or press Ctrl-C in it) and start it",
  );
  expect(sessionApi.end).not.toHaveBeenCalled();
  // Cancel is the safe default, and does nothing.
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(sessionApi.end).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Session" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "End session…" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "End session" }));
  await waitFor(() => expect(sessionApi.end).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(ended).toHaveBeenCalledTimes(1));
});

it("End session says what's still going, which a restart would stop", async () => {
  expect(activityWarning({ agent_turn: false, workflow_run: false })).toBeNull();
  expect(activityWarning({ agent_turn: true, workflow_run: false })).toBe(
    "An agent turn is still going; it carries on, but restarting DataLab to get back in will stop it.",
  );
  expect(activityWarning({ agent_turn: false, workflow_run: true })).toBe(
    "A workflow run is still going; it carries on, but restarting DataLab to get back in will stop it.",
  );
  vi.mocked(sessionApi.activity).mockResolvedValue({ agent_turn: false, workflow_run: true });
  show(<SessionMenu onEnded={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Session" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "End session…" }));
  const dialog = screen.getByRole("dialog", { name: "End this session?" });
  expect(await within(dialog).findByText(/A workflow run is still going/)).toBeInTheDocument();
});

it("a popover closes when focus moves out of it", async () => {
  show(
    <>
      <DatabaseShortcut />
      <button>Elsewhere</button>
    </>,
  );
  fireEvent.click(await screen.findByRole("button", { name: /^Database/ }));
  const panel = screen.getByRole("dialog");
  fireEvent.blur(panel.querySelector("button, a")!, { relatedTarget: screen.getByRole("button", { name: "Elsewhere" }) });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

// ------------------------------------------------------------ attention, below 2xl

it("attention: nothing to say, no button", async () => {
  show(<AttentionMenu />);
  await waitFor(() => expect(settingsApi.connections).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: /attention/i })).not.toBeInTheDocument();
});

it("attention: one thing is said on the button itself", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ key: "missing" }));
  show(<AttentionMenu />);
  const button = await screen.findByRole("button", { name: "Needs attention: Key missing" });
  expect(button).toHaveTextContent("Key missing");
  expect(button.parentElement).toHaveClass("2xl:hidden");
});

it("attention: several fold into one 'N need attention' menu listing each in words", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ key: "missing", password: "missing" }));
  vi.mocked(api.destinations).mockResolvedValue([folder({ available: false, status: "missing" })] as never);
  vi.mocked(githubApi.status).mockResolvedValue({ available: true, signed_in: false, account: null, message: null, repos: [] } as never);
  show(<AttentionMenu />);
  const button = await screen.findByRole("button", { name: /^Needs attention/ });
  await waitFor(() => expect(button).toHaveTextContent("4 need attention"));
  fireEvent.click(button);
  const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
  expect(items.map((i) => i.querySelector("span span")?.textContent)).toEqual([
    "DB: no password",
    "Key missing",
    "GitHub: sign in",
    "Folder unavailable",
  ]);
  fireEvent.click(items[1]);
  expect(screen.getByTestId("at")).toHaveTextContent('/settings/connections#connection-umgpt {"highlight":"connection-umgpt"}');
});

it("each shortcut says its own words only from 2xl up (the attention menu says them below)", async () => {
  vi.mocked(settingsApi.connections).mockResolvedValue(connections({ key: "missing" }));
  show(<KeyShortcut />);
  const words = await screen.findByText("Key missing");
  expect(words).toHaveClass("hidden", "2xl:inline");
});

it("practice's database standing follows how it's starting, and a problem needs attention", () => {
  const practice = {
    profile: "practice",
    settings_file: "s",
    oracle: { configured: true, practice: true, dsn: "x", user: "u", read_only_roles: [], allowed_schemas: [], password: null, can_set_password: false },
    model: { base_url: "x", key: "keychain", can_set_key: false },
    read_only_because: null,
  } as Connections; // prettier-ignore
  expect(databaseStanding(practice, undefined, "loading").state).toBe("practice database starting…");
  expect(databaseStanding(practice, undefined, "problem").attention).toBe("DB: not running");
  expect(databaseStanding(practice, undefined, "waiting-for-docker").attention).toBe("DB: waiting for Docker");
  expect(whilePracticeDatabaseStarts({ state: { data: { practice_database: "waiting-for-docker" } as Health } })).toBe(3000);
  expect(whilePracticeDatabaseStarts({ state: { data: { practice_database: "problem" } as Health } })).toBe(false);
  expect(databaseStanding(practice, undefined, "ready").attention).toBeUndefined();
  expect(databaseStanding({ ...practice, oracle: { ...practice.oracle, practice: false } }, undefined, "problem").attention).toBeUndefined();
});
