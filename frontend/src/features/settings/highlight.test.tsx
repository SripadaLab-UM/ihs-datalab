// A link into Settings from elsewhere lands on the part it's about: scrolled to,
// its heading focused, lit up briefly and announced. Opening Settings, or its
// own section list, lights nothing up.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { settingsApi, type UpdateCheck } from "@/api/settings";

import { HIGHLIGHT_MS, type HighlightId, settingsLink } from "./highlight";
import type { SettingsSectionId } from "./sectionIds";
import { SettingsPage } from "./SettingsPage";

vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(async () => ({ profile: "real", version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 1 })),
    lastSafetyReport: vi.fn(async () => null),
    destinations: vi.fn(async () => []),
    destinationPlaces: vi.fn(async () => ({ can_add: true, why_not: null, places: [] })),
    catalogStatus: vi.fn(async () => ({ detail: null })),
  },
}));
vi.mock("@/api/github", () => ({
  githubApi: {
    status: vi.fn(async () => ({ available: true, signed_in: false, account: null, repos: [] })),
    startSignIn: vi.fn(),
    pollSignIn: vi.fn(),
    cancelSignIn: vi.fn(),
    signOut: vi.fn(),
  },
}));
vi.mock("@/api/knowledge", () => ({ knowledgeApi: { status: vi.fn(() => new Promise(() => {})), sync: vi.fn() } }));
vi.mock("@/api/pipelines", () => ({ pipelinesApi: { status: vi.fn(() => new Promise(() => {})), sync: vi.fn() } }));
vi.mock("@/api/settings", async (original) => ({
  ...(await original<typeof import("@/api/settings")>()),
  settingsApi: {
    connections: vi.fn(async () => ({
      profile: "real",
      settings_file: "/s/settings.toml",
      oracle: {
        configured: true, practice: false, dsn: "db:1521/S", user: "U", read_only_roles: [], allowed_schemas: [],
        password: "keychain", can_set_password: true,
      },
      model: { base_url: "https://api.example/v1", key: "keychain", can_set_key: true },
      read_only_because: null,
    })), // prettier-ignore
    testConnections: vi.fn(),
    storage: vi.fn(() => new Promise(() => {})),
    updates: vi.fn(),
    updateCheck: vi.fn(),
    destinationKeys: vi.fn(async () => []),
    diagnostics: vi.fn(),
  },
}));

const CHECK: UpdateCheck = {
  state: "up-to-date", message: "DataLab 0.1.0 is the newest release.", current_version: "0.1.0", channel: "auto",
  checked_at: null, available: null, can_install: false, cannot_install_because: null,
  install: { state: "idle", version: null, message: "", started_at: null, updated_at: null }, updating: false,
  check_on_start: true, check_every_hour: true,
}; // prettier-ignore

let reduced = false;
const scrolled: Element[] = [];
const scrolls: { element: Element; options: unknown }[] = [];

beforeEach(() => {
  reduced = false;
  scrolled.length = 0;
  scrolls.length = 0;
  localStorage.clear();
  window.matchMedia = vi.fn((query: string) => ({ matches: query.includes("reduce") ? reduced : false })) as never;
  Element.prototype.scrollIntoView = vi.fn(function (this: Element, options?: unknown) {
    scrolled.push(this);
    scrolls.push({ element: this, options });
  });
  vi.mocked(settingsApi.updateCheck).mockResolvedValue(CHECK);
  vi.mocked(settingsApi.updates).mockResolvedValue({
    version: "0.1.0", check: CHECK, marker: null, marker_unreadable: false, recovery: null, history: [],
    set_aside_notes: [], backups: [], migrations_applied: 1, latest_migration: null,
  }); // prettier-ignore
});
afterEach(() => vi.useRealTimers());

/** Somewhere outside Settings, with the links that point into it. */
function Elsewhere() {
  const links: [string, SettingsSectionId, HighlightId][] = [
    ["Update available", "updates", "updates-check"],
    ["GitHub", "connections", "connection-github"],
    ["Add one in Settings", "export-folders", "export-folders"],
    ["Workflow destinations", "export-folders", "destination-keys"],
    ["Theme", "appearance", "appearance"],
  ];
  return (
    <nav aria-label="elsewhere">
      {links.map(([text, section, id]) => (
        <Link key={id} {...settingsLink(section, id)}>
          {text}
        </Link>
      ))}
      <Link to="/settings/updates">Plain Settings link</Link>
    </nav>
  );
}

/** Back and Forward, as the browser's own buttons would. */
function History() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(-1)}>Back</button>
      <button onClick={() => navigate(1)}>Forward</button>
    </>
  );
}

function show(at = "/elsewhere") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <History />
        <Routes>
          <Route path="elsewhere" element={<Elsewhere />} />
          <Route path="settings/:section?" element={<SettingsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lit = () => document.querySelectorAll(".dl-highlight, .dl-highlight-still");

it.each([
  ["Update available", "updates-check", "Updates", "Settings: Updates, Check now"],
  ["GitHub", "connection-github", "GitHub", "Settings: Connections, GitHub"],
  ["Add one in Settings", "export-folders", "Export folders", "Settings: Export folders"],
  ["Workflow destinations", "destination-keys", "Workflow destinations", "Settings: Export folders, Workflow destinations"],
  ["Theme", "appearance", "Appearance", "Settings: Appearance"],
])("%s lands on #%s, lit, with its heading focused and announced", async (link, id, heading, said) => {
  show();
  fireEvent.click(screen.getByRole("link", { name: link }));
  await waitFor(() => expect(document.getElementById(id)).toHaveClass("dl-highlight"));
  expect(scrolled).toContain(document.getElementById(id));
  expect(document.activeElement).toHaveTextContent(heading);
  expect(document.activeElement?.matches("h2, h3")).toBe(true);
  await waitFor(() => expect(screen.getByTestId("settings-announcement")).toHaveTextContent(said));
  expect(lit()).toHaveLength(1);
});

it("leaves the highlight's own scroll as the last word, once the page settles", async () => {
  show();
  fireEvent.click(screen.getByRole("link", { name: "Update available" }));
  const target = () => document.getElementById("updates-check");
  await waitFor(() => expect(target()).toHaveClass("dl-highlight"));
  // Health and the section's data arrive, the navigation's state is dropped: none of it scrolls again.
  await act(() => new Promise((r) => setTimeout(r, 300)));
  expect(scrolls.at(-1)).toEqual({ element: target(), options: { block: "center", behavior: "smooth" } });
});

it("clears the announcement once the highlight ends, so a repeat is heard", async () => {
  show();
  fireEvent.click(screen.getByRole("link", { name: "Theme" }));
  await waitFor(() => expect(screen.getByTestId("settings-announcement")).toHaveTextContent("Settings: Appearance"));
  await act(() => new Promise((r) => setTimeout(r, HIGHLIGHT_MS + 50)));
  expect(screen.getByTestId("settings-announcement")).toHaveTextContent("");
});

it("doesn't light up again going back or forward onto the entry", async () => {
  show();
  fireEvent.click(screen.getByRole("link", { name: "Theme" }));
  await waitFor(() => expect(lit()).toHaveLength(1));
  await act(() => new Promise((r) => setTimeout(r, HIGHLIGHT_MS + 50)));
  const nav = screen.getByRole("navigation", { name: "Settings sections" });
  fireEvent.click(within(nav).getByRole("link", { name: "Updates" }));
  expect(await screen.findByRole("heading", { level: 2, name: "Updates" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByRole("heading", { level: 2, name: "Appearance" })).toBeTruthy();
  await act(() => new Promise((r) => setTimeout(r, 100)));
  expect(lit()).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByRole("link", { name: "Theme" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Forward" }));
  expect(await screen.findByRole("heading", { level: 2, name: "Appearance" })).toBeTruthy();
  await act(() => new Promise((r) => setTimeout(r, 100)));
  expect(lit()).toHaveLength(0);
});

it("leaves focus where the person put it while the part was loading", async () => {
  const data = await settingsApi.updates();
  vi.mocked(settingsApi.updates).mockImplementation(() => new Promise((r) => setTimeout(() => r(data), 300)));
  show();
  fireEvent.click(screen.getByRole("link", { name: "Update available" }));
  // Before the Updates data arrives, the person moves on to the section list.
  const nav = await screen.findByRole("navigation", { name: "Settings sections" });
  expect(document.getElementById("updates-check")).toBeNull();
  within(nav).getByRole("link", { name: "Storage" }).focus();
  await waitFor(() => expect(document.getElementById("updates-check")).toHaveClass("dl-highlight"));
  expect(document.activeElement).toHaveTextContent("Storage");
});

it("fades the highlight out after its time", async () => {
  show();
  fireEvent.click(screen.getByRole("link", { name: "Theme" }));
  await waitFor(() => expect(lit()).toHaveLength(1));
  await act(() => new Promise((r) => setTimeout(r, HIGHLIGHT_MS + 50)));
  expect(lit()).toHaveLength(0);
});

it("holds still under reduced motion", async () => {
  reduced = true;
  show();
  fireEvent.click(screen.getByRole("link", { name: "Update available" }));
  await waitFor(() => expect(document.getElementById("updates-check")).toHaveClass("dl-highlight-still"));
  expect(document.querySelector(".dl-highlight")).toBeNull();
  await act(() => new Promise((r) => setTimeout(r, HIGHLIGHT_MS + 50)));
  expect(lit()).toHaveLength(0);
});

it("lights nothing on a plain link, a deep link, or Settings' own section list", async () => {
  show();
  fireEvent.click(screen.getByRole("link", { name: "Plain Settings link" }));
  expect(await screen.findByRole("heading", { level: 2, name: "Updates" })).toBeTruthy();
  const nav = screen.getByRole("navigation", { name: "Settings sections" });
  fireEvent.click(within(nav).getByRole("link", { name: "Appearance" }));
  expect(await screen.findByRole("heading", { level: 2, name: "Appearance" })).toBeTruthy();
  await act(() => new Promise((r) => setTimeout(r, 50)));
  expect(lit()).toHaveLength(0);
  expect(screen.getByTestId("settings-announcement")).toHaveTextContent("");
});

it("still scrolls a deep link to its place, and an old anchor to the part's new id", async () => {
  show("/settings/connections#github");
  await waitFor(() => expect(scrolled).toContain(document.getElementById("connection-github")));
  expect(lit()).toHaveLength(0);
});
