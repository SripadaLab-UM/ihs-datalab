import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type SafetyReport } from "@/api/client";
import { githubApi } from "@/api/github";
import { settingsApi } from "@/api/settings";

import { LAST_SECTION_KEY } from "./sectionIds";
import { SettingsPage } from "./SettingsPage";

vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(),
    lastSafetyReport: vi.fn(),
    runSafetyCheck: vi.fn(),
    destinations: vi.fn(async () => []),
    catalogStatus: vi.fn(async () => ({ detail: null })),
  },
}));

vi.mock("@/api/github", () => ({
  githubApi: { status: vi.fn(), signIn: vi.fn(), startSignIn: vi.fn(), pollSignIn: vi.fn(), cancelSignIn: vi.fn(), signOut: vi.fn() },
}));
vi.mock("@/api/knowledge", () => ({ knowledgeApi: { status: vi.fn(() => new Promise(() => {})), sync: vi.fn() } }));
vi.mock("@/api/pipelines", () => ({ pipelinesApi: { status: vi.fn(() => new Promise(() => {})), sync: vi.fn() } }));

vi.mock("@/api/settings", async (original) => ({
  ...(await original<typeof import("@/api/settings")>()),
  settingsApi: {
    connections: vi.fn(),
    testConnections: vi.fn(),
    storage: vi.fn(() => new Promise(() => {})),
    updates: vi.fn(() => new Promise(() => {})),
    updateCheck: vi.fn(() => new Promise(() => {})),
    destinationKeys: vi.fn(async () => []),
    diagnostics: vi.fn(),
  },
}));

const PRACTICE_HEALTH = { profile: "practice", version: "0.1.0", status: "ok", database_configured: true, catalog_tables: 1234 };
const REAL_HEALTH = { ...PRACTICE_HEALTH, profile: "real" };

const REAL_CONNECTIONS = {
  profile: "real",
  settings_file: "/Users/me/Library/Application Support/DataLab/real/settings.toml",
  oracle: {
    configured: true,
    practice: false,
    dsn: "db.example:1521/SVC",
    user: "SVC_READER",
    read_only_roles: ["IHS_2025_RO"],
    allowed_schemas: ["IHS_2025"],
    password: "keychain",
    can_set_password: true,
  },
  model: { base_url: "https://api.example/v1", key: "keychain", can_set_key: true },
  read_only_because: null,
} as const;

const PRACTICE_CONNECTIONS = {
  ...REAL_CONNECTIONS,
  profile: "practice",
  oracle: { ...REAL_CONNECTIONS.oracle, practice: true, password: null, can_set_password: false },
  model: { ...REAL_CONNECTIONS.model, key: "missing", can_set_key: false },
  read_only_because: "Practice DataLab uses the local synthetic database, whose settings are fixed.",
} as const;

function report(results: SafetyReport["results"], passed: boolean): SafetyReport {
  return {
    passed,
    started_at: "2026-09-27T14:00:00Z",
    finished_at: "2026-09-27T14:00:20Z",
    results,
  } as SafetyReport;
}
const check = (id: string, status: "pass" | "fail" | "skip", required = true) => ({
  id,
  promise: "Sealed sessions",
  label: `Check ${id}`,
  status,
  detail: status === "fail" ? `Check ${id} broke out.` : "",
  required,
});

function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.hash}`}</output>;
}

function show(at = "/settings") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route
            path="/settings/:section?"
            element={
              <>
                <SettingsPage />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const where = () => screen.getByTestId("where").textContent;
const nav = () => screen.getByRole("navigation", { name: "Settings sections" });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.health).mockResolvedValue(PRACTICE_HEALTH as never);
  vi.mocked(api.lastSafetyReport).mockResolvedValue(null);
  vi.mocked(settingsApi.connections).mockResolvedValue(PRACTICE_CONNECTIONS as never);
  vi.mocked(githubApi.status).mockResolvedValue({ available: true, message: null, signed_in: false, account: null, repos: [] });
  vi.mocked(githubApi.signIn).mockResolvedValue({
    state: "signed out", user_code: null, verification_uri: null, expires_at: null, interval: null, account: null, message: null,
  }); // prettier-ignore
});

afterEach(() => vi.restoreAllMocks());

it("opens on Connections, with every section in the list", async () => {
  show();
  await waitFor(() => expect(where()).toBe("/settings/connections"));
  const links = within(nav()).getAllByRole("link");
  expect(links.map((l) => l.textContent)).toEqual([
    "Connections",
    "Appearance",
    "Export folders",
    "Updates",
    "Storage",
    "Safety",
    "About",
  ]);
  expect(within(nav()).getByRole("link", { name: "Connections" }).getAttribute("aria-current")).toBe("page");
  expect(await screen.findByRole("heading", { level: 2, name: "Connections" })).toBeTruthy();
  // Only the chosen section is on the page.
  expect(screen.queryByRole("heading", { name: "Safety check" })).toBeNull();
});

it("goes to a section from the list, and remembers it for next time", async () => {
  const first = show();
  await screen.findByRole("heading", { level: 2, name: "Connections" });
  fireEvent.click(within(nav()).getByRole("link", { name: "Storage" }));
  expect(where()).toBe("/settings/storage");
  expect(screen.getByRole("heading", { level: 2, name: "Storage" })).toBeTruthy();
  expect(localStorage.getItem(LAST_SECTION_KEY)).toBe("storage");
  first.unmount();
  show();
  await waitFor(() => expect(where()).toBe("/settings/storage"));
});

it("opens the section a link names, and a place in it", async () => {
  show("/settings/export-folders");
  expect(await screen.findByRole("heading", { level: 2, name: "Export folders" })).toBeTruthy();
  expect(await screen.findByRole("heading", { level: 2, name: "Workflow destinations" })).toBeTruthy();
  expect(within(nav()).getByRole("link", { name: "Export folders" }).getAttribute("aria-current")).toBe("page");
});

it("sends links made before sections had addresses to their section", async () => {
  show("/settings#destination-keys");
  await waitFor(() => expect(where()).toBe("/settings/export-folders#destination-keys"));
  await waitFor(() => expect(document.getElementById("destination-keys")).toBeTruthy());
});

it("sends /settings#updates to Updates", async () => {
  show("/settings#updates");
  await waitFor(() => expect(where()).toBe("/settings/updates"));
});

it("goes to Connections for a section it doesn't know, or a malformed link", async () => {
  show("/settings/nowhere");
  await waitFor(() => expect(where()).toBe("/settings/connections"));
});

it("ignores a malformed link to a section", async () => {
  show("/settings#%E0%A4%A");
  await waitFor(() => expect(where()).toBe("/settings/connections"));
});

it("still works when the browser refuses storage", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("SecurityError");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("SecurityError");
  });
  show();
  await waitFor(() => expect(where()).toBe("/settings/connections"));
  fireEvent.click(within(nav()).getByRole("link", { name: "Appearance" }));
  expect(screen.getByRole("heading", { level: 2, name: "Appearance" })).toBeTruthy();
});

it("moves between sections with the arrow keys", async () => {
  show("/settings/connections");
  const connections = within(nav()).getByRole("link", { name: "Connections" });
  connections.focus();
  fireEvent.keyDown(connections, { key: "ArrowDown" });
  expect(document.activeElement?.textContent).toBe("Appearance");
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(document.activeElement?.textContent).toBe("About");
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  expect(document.activeElement?.textContent).toBe("Connections");
  fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
  expect(document.activeElement?.textContent).toBe("About");
  // Enter follows the focused link, as any link does.
});

it("practice labels what it fixes instead of leaving it out", async () => {
  show("/settings/connections");
  expect(await screen.findByText("Fixed on the practice DataLab: the synthetic database")).toBeTruthy();
  expect(screen.getByText("Fixed on the practice DataLab")).toBeTruthy(); // the U-M GPT key
  expect(await screen.findByText("Not used on the practice DataLab")).toBeTruthy(); // GitHub
  expect(screen.queryByRole("button", { name: /^(Save|Replace) key$/ })).toBeNull();
});

it("practice's export folders are labelled as fixed", async () => {
  show("/settings/export-folders");
  expect(await screen.findByText(/exports only to its own practice folder/)).toBeTruthy();
  expect(screen.getAllByText("Fixed on the practice DataLab").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByRole("button", { name: /Add a folder/ })).toBeNull();
});

it("the real profile offers every setting practice fixes", async () => {
  vi.mocked(api.health).mockResolvedValue(REAL_HEALTH as never);
  vi.mocked(settingsApi.connections).mockResolvedValue(REAL_CONNECTIONS as never);
  vi.mocked(githubApi.status).mockResolvedValue({
    available: true,
    message: null,
    signed_in: true,
    account: { login: "yfang", name: "Yu Fang" },
    repos: [{ area: "knowledge", name: "SripadaLab-UM/ihs-knowledge" }],
  });
  const view = show("/settings/connections");
  const gpt = await screen.findByRole("region", { name: "U-M GPT" });
  expect(within(gpt).getByText("Saved in keychain")).toBeTruthy();
  expect(within(gpt).getByRole("button", { name: "Replace key" })).toBeTruthy();
  expect(within(gpt).getByRole("button", { name: "Test connection" })).toBeTruthy();
  const database = screen.getByRole("region", { name: "The study database" });
  expect(within(database).getByRole("button", { name: "Replace password" })).toBeTruthy();
  const github = screen.getByRole("region", { name: "GitHub" });
  expect(await within(github).findByText("Yu Fang")).toBeTruthy();
  expect(within(github).getByRole("button", { name: "Switch account…" })).toBeTruthy();
  expect(within(github).getByRole("button", { name: "Sign out" })).toBeTruthy();
  expect(screen.queryByText(/Fixed on the practice/)).toBeNull();
  view.unmount();

  show("/settings/export-folders");
  expect(await screen.findByRole("button", { name: "Add a folder…" })).toBeTruthy();
  expect(screen.queryByText(/Fixed on the practice/)).toBeNull();
});

it("Safety: says it hasn't run yet, with the button to run it", async () => {
  show("/settings/safety");
  const summary = await screen.findByRole("status", { name: "Safety check summary" });
  await waitFor(() => expect(summary.textContent).toBe("Not run yet"));
  expect(screen.getByRole("button", { name: "Run safety check" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Details" })).toBeNull();
});

it("Safety: every check passed, with when, and the report folded away", async () => {
  vi.mocked(api.lastSafetyReport).mockResolvedValue(report([check("a", "pass"), check("b", "pass")], true));
  show("/settings/safety");
  const summary = await screen.findByRole("status", { name: "Safety check summary" });
  await waitFor(() => expect(summary.textContent).toContain("Every check passed"));
  expect(summary.textContent).toContain("· checked");
  expect(within(summary).getByText(new Date("2026-09-27T14:00:20Z").toLocaleString())).toBeTruthy();
  const details = screen.getByRole("button", { name: "Details" });
  expect(details.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Check a")).toBeNull();
  fireEvent.click(details);
  expect(details.getAttribute("aria-expanded")).toBe("true");
  // The report as it always was.
  expect(screen.getByText("Check a")).toBeTruthy();
  expect(screen.getAllByText("Every check passed")).toHaveLength(2);
  // The list marks Safety as fine.
  expect(within(nav()).getByRole("img", { name: "Every check passed" })).toBeTruthy();
});

it("Safety: counts the problems, and the report keeps its words", async () => {
  vi.mocked(api.lastSafetyReport).mockResolvedValue(
    report([check("a", "fail"), check("b", "fail"), check("c", "pass")], false),
  );
  show("/settings/safety");
  const summary = await screen.findByRole("status", { name: "Safety check summary" });
  await waitFor(() => expect(summary.textContent).toContain("2 problems"));
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(screen.getByText("2 checks failed")).toBeTruthy();
  expect(screen.getByText("Check a broke out.")).toBeTruthy();
  expect(within(nav()).getByRole("img", { name: "2 problems" })).toBeTruthy();
});

it("Safety: a report that didn't pass with nothing counted says so, not 0 problems", async () => {
  vi.mocked(api.lastSafetyReport).mockResolvedValue(report([check("a", "pass")], false));
  show("/settings/safety");
  const summary = await screen.findByRole("status", { name: "Safety check summary" });
  await waitFor(() => expect(summary.textContent).toContain("Didn't pass"));
  expect(summary.textContent).not.toContain("0 problems");
});

it("Safety: a check that couldn't be verified is said so", async () => {
  vi.mocked(api.lastSafetyReport).mockResolvedValue(report([check("a", "pass"), check("b", "skip")], true));
  show("/settings/safety");
  const summary = await screen.findByRole("status", { name: "Safety check summary" });
  await waitFor(() => expect(summary.textContent).toContain("1 check couldn't be verified"));
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(screen.getByText("No failures, but 1 check couldn't be verified")).toBeTruthy();
});

it("Safety: a run that finds a problem opens its report", async () => {
  vi.mocked(api.runSafetyCheck).mockResolvedValue(report([check("a", "fail")], false));
  show("/settings/safety");
  fireEvent.click(await screen.findByRole("button", { name: "Run safety check" }));
  const summary = screen.getByRole("status", { name: "Safety check summary" });
  await waitFor(() => expect(summary.textContent).toContain("1 problem"));
  expect(screen.getByRole("button", { name: "Details" }).getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("1 check failed")).toBeTruthy();
});

it("About has the catalog and Copy diagnostics", async () => {
  show("/settings/about");
  expect(await screen.findByText("1,234 tables and views")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy diagnostics" })).toBeTruthy();
});
