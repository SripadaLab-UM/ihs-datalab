// The connections walkthrough (17-connections.md), filmed on a real-profile
// DataLab with an empty data folder (FILM_REAL=1; see footage/app.mjs).
// Shots 4.2 and 6.3 need the VPN on; the others are filmed with it off:
//
//   node footage/film-walkthrough.mjs 17-connections 2.1 2.2 3.1 3.2 4.1 4.3 4.4 5.1 6.1 6.2   # VPN off
//   node footage/film-walkthrough.mjs 17-connections 4.2 6.3                                   # VPN on
//
// Nothing is typed into a key or password field, and nothing is saved.

const header = (page) => page.locator("header").first();
const hb = (page, name) => header(page).getByRole("button", { name });
const hl = (page, name) => header(page).getByRole("link", { name });
const pop = (page) => page.locator("[role=dialog]").last();
const status = (page) => hb(page, /^Database:/).locator("xpath=..");
const db = (page) => page.locator("#connection-database");
const gpt = (page) => page.locator("#connection-umgpt");
const byHeading = (page, name) => page.locator("section").filter({ has: page.getByRole("heading", { name, exact: true }) }).last();
const nav = (page, name) => page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: new RegExp(`^${name}`) });
const result = (section) => section.getByText(/Can't reach|Connected|accepted the key|failed|rejected|couldn't/i).last();

async function openPop(page, locator, pause) {
  await locator.hover();
  await pause(0.25);
  await locator.click();
  await pause(0.6);
}

async function connections(page, BASE, pause) {
  await page.goto(`${BASE}/settings/connections`);
  await db(page).waitFor();
  await pause(0.8);
}

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(`${BASE}/workspace`);
        await header(page).waitFor();
        await pause(1);
      },
      async act(page, { until }) {
        await until("only works once", 1.2);
      },
      targets: { header },
    },
    "2.2": {
      async act(page, { until }) {
        await until("before you start", 1);
      },
      targets: { status },
    },
    "3.1": {
      async act(page, { until, mark, pause }) {
        await until("The first is the database", -0.3);
        await openPop(page, hb(page, /^Database:/), pause);
        await mark("db");
        await until("the AI model service's key", -0.4);
        await page.keyboard.press("Escape");
        await openPop(page, hb(page, /^U-M GPT key:/), pause);
        await mark("gpt");
        await until("Then GitHub", -0.2);
        await page.keyboard.press("Escape");
        await hl(page, /^GitHub:/).hover();
        await mark("github");
        await until("your export folders", -0.4);
        await openPop(page, hb(page, /^Export folders:/), pause);
        await mark("folders");
        await until("links to its settings", 1);
        await page.keyboard.press("Escape");
      },
      targets: {
        db: (page) => page.locator("[role=dialog]").filter({ hasText: "Open connection settings" }).last(),
        gpt: (page) => page.locator("[role=dialog]").filter({ hasText: "Test both connections" }).last(),
        github: (page) => hl(page, /^GitHub:/),
        folders: (page) => page.locator("[role=dialog]").filter({ hasText: "Manage folders" }).last(),
      },
    },
    "3.2": {
      async act(page, { until, mark, pause }) {
        await hl(page, /^Help:/).hover();
        await mark("help");
        await until("The More menu", -0.3);
        await openPop(page, hb(page, "More"), pause);
        await mark("more");
        await until("Session", -0.4);
        await page.keyboard.press("Escape");
        await openPop(page, hb(page, "Session"), pause);
        await mark("session");
        await until("in this browser", 1);
        await page.keyboard.press("Escape");
      },
      targets: {
        help: (page) => hl(page, /^Help:/),
        more: (page) => page.locator("[role=menu], [role=dialog]").last(),
        session: (page) => page.locator("[role=menu], [role=dialog]").last(),
      },
    },
    "4.1": {
      async prepare(page, { BASE, pause }) {
        await connections(page, BASE, pause);
      },
      async act(page, { until, mark, pause }) {
        const test = db(page).getByRole("button", { name: "Test connection" });
        await test.scrollIntoViewIfNeeded();
        await mark("dbtest");
        await until("press Test connection", 0.2);
        await test.click();
        await result(db(page)).waitFor({ timeout: 60_000 });
        await pause(0.4);
        await mark("dberror");
        await until("then test again", 1.2);
      },
      targets: { dbtest: (page) => db(page).getByRole("button", { name: "Test connection" }), dberror: (page) => result(db(page)) },
    },
    "4.2": {
      async prepare(page, { BASE, pause }) {
        await connections(page, BASE, pause);
      },
      async act(page, { until, mark, pause }) {
        const test = db(page).getByRole("button", { name: "Test connection" });
        await mark("dbtest");
        await until("test again", 0.2);
        await test.click();
        await result(db(page)).waitFor({ timeout: 60_000 });
        await pause(0.5);
        await mark("dbresult");
        await until("switched on", 1.2);
      },
      targets: { dbtest: db, dbresult: (page) => result(db(page)) },
    },
    "4.3": {
      async prepare(page, { BASE, pause }) {
        await connections(page, BASE, pause);
      },
      async act(page, { until, mark, pause }) {
        const test = gpt(page).getByRole("button", { name: "Test connection" });
        await mark("gpttest");
        await until("Test the AI model service", 0.4);
        await test.click();
        await result(gpt(page)).waitFor({ timeout: 60_000 });
        await pause(0.4);
        await mark("gptresult");
        await until("press Replace key", -0.1);
        await gpt(page).getByRole("button", { name: /^Replace key/ }).click();
        await pause(0.6);
        await mark("gptkey");
        await until("never shows it again", 1);
        await gpt(page).getByRole("button", { name: "Cancel" }).click();
      },
      targets: { gpttest: (page) => gpt(page).getByRole("button", { name: "Test connection" }), gptresult: (page) => result(gpt(page)), gptkey: gpt },
    },
    "4.4": {
      async prepare(page, { BASE, pause }) {
        await connections(page, BASE, pause);
      },
      async act(page, { until, mark, pause }) {
        // A short shot: the form opens at once, before its cue.
        await db(page).evaluate((n) => n.scrollIntoView({ block: "center" }));
        await db(page).getByRole("button", { name: /^Replace password/ }).click();
        await pause(0.5);
        await mark("dbpass");
        await until("the same way", 1.2);
        await db(page).getByRole("button", { name: "Cancel" }).click();
      },
      targets: { dbpass: db },
    },
    "5.1": {
      async act(page, { until, mark, pause }) {
        const gh = page.locator("#connection-github");
        await gh.evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "start" }));
        await pause(0.8);
        await mark("github");
        await until("Sync downloads", -0.3);
        await mark("repos");
        await until("Sign out", 1.5);
      },
      targets: {
        github: (page) => page.locator("#connection-github"),
        repos: (page) => page.locator("#connection-github").getByText(/ACCESS TO THE LAB'S REPOS/i).locator("xpath=.."),
      },
    },
    "6.1": {
      async act(page, { until, mark, pause }) {
        await nav(page, "Export folders").click();
        await pause(0.8);
        await until("Add a folder", -0.2);
        await mark("add");
        await until("no account or key", 1.2);
      },
      targets: { add: (page) => byHeading(page, "Add a folder").or(page.getByText("Add a folder").first().locator("xpath=..")).first() },
    },
    "6.2": {
      async prepare(page, { BASE, pause }) {
        await page.goto(`${BASE}/settings/export-folders`);
        await pause(0.8);
      },
      async act(page, { until, mark, pause }) {
        await nav(page, "Updates").click();
        await pause(0.5);
        await mark("updates");
        await until("press Check now", 0);
        await page.getByRole("button", { name: "Check now" }).click();
        await pause(0.4);
        await until("Storage shows", 0.1);
        await nav(page, "Storage").click();
        await pause(0.8);
        await mark("storage");
        await until("deleted automatically", 1.2);
      },
      targets: { updates: (page) => byHeading(page, "Updates"), storage: (page) => byHeading(page, "Storage") },
    },
    "6.3": {
      async prepare(page, { BASE, pause }) {
        await page.goto(`${BASE}/settings/safety`);
        await page.getByRole("button", { name: "Run safety check" }).waitFor();
        await pause(0.8);
      },
      async act(page, { until, mark, pause, fastForward }) {
        await until("Run the Safety check", 0.2);
        await page.getByRole("button", { name: "Run safety check" }).click();
        // Sped up until the check has finished and its button is back.
        await fastForward(async () => {
          await page.getByRole("button", { name: "Run safety check", exact: true }).waitFor({ timeout: 240_000 });
          await page.getByText(/Every check passed|checks? failed|couldn't be verified/).first().waitFor({ timeout: 60_000 });
          await pause(0.8);
        }, "then, under About");
        await mark("safety");
        // The check ran long in real time, so later cues are already due:
        // hold on its result before moving on.
        await pause(2);
        await nav(page, "About").click();
        await page.locator("#diagnostics").waitFor();
        await pause(0.5);
        await mark("diagnostics");
        await until("or conversations", 1);
      },
      targets: {
        safety: (page) => page.getByText(/Every check passed|checks? failed|couldn't be verified/).first().locator("xpath=.."),
        diagnostics: (page) => page.locator("#diagnostics"),
      },
    },
  },
};
