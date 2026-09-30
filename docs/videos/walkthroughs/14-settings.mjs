// The Settings & Safety walkthrough (14-settings.md). It leads with the AI's
// connections, then runs the Safety check live.

const go = (page, BASE, section) => page.goto(`${BASE}/settings/${section}`);
// Links are matched by how their name starts: Safety's carries a status dot.
const nav = (page, name) => page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: new RegExp(`^${name}`) });
const byHeading = (page, name) =>
  page.locator("section").filter({ has: page.getByRole("heading", { name, exact: true }) }).last();
const boxed = (locator) => locator.locator("xpath=ancestor::*[contains(@class,'border')][1]");

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await go(page, BASE, "connections");
        await page.locator("#connection-umgpt").waitFor();
        await pause(1);
      },
      async act(page, { until }) {
        await until("never sees them", 1.2);
      },
      targets: {
        gpt: (page) => page.locator("#connection-umgpt"),
        database: (page) => page.locator("#connection-database"),
      },
    },
    "2.2": {
      async act(page, { until, mark, pause }) {
        await mark("roles");
        await until("Test connection", -0.3);
        const test = page.locator("#connection-database").getByRole("button", { name: "Test connection" });
        await test.hover();
        await pause(0.3);
        await test.click();
        await pause(1.5);
        await mark("test");
        await until("checks each one", 1.2);
      },
      targets: {
        roles: (page) => page.getByText("IHS_2025_RO").first().locator("xpath=.."),
        test: (page) => page.locator("#connection-database").getByRole("button", { name: "Test connection" }),
      },
    },
    "3.1": {
      async act(page, { until, mark, pause, fastForward }) {
        await pause(0.3);
        await nav(page, "Safety").click();
        const run = page.getByRole("button", { name: "Run safety check" });
        await run.waitFor();
        await mark("run");
        await until("Press Run safety check", 0.4);
        await run.hover();
        await pause(0.3);
        await run.click();
        await fastForward(async () => {
          await page.getByRole("button", { name: "Run safety check" }).waitFor({ timeout: 180_000 });
          await page.getByText(/Every check passed|failed|couldn't be verified/).first().waitFor({ timeout: 180_000 });
          await pause(1);
        }, "every promise");
        await mark("result");
        await until("every promise", 1.2);
      },
      targets: {
        run: (page) => page.getByRole("button", { name: "Run safety check" }),
        result: (page) => page.getByText(/Every check passed|failed|couldn't be verified/).first().locator("xpath=.."),
      },
    },
    "3.2": {
      async act(page, { until, mark, pause }) {
        await page.getByRole("button", { name: "Details" }).click();
        await pause(0.8);
        await mark("details");
        await until("can't write", -1.5);
        await page.mouse.wheel(0, 380);
        await pause(0.8);
        await mark("details");
        await until("and more", 1.2);
      },
      targets: { details: (page) => byHeading(page, "Safety check") },
    },
    "4.1": {
      async act(page, { until, mark, pause }) {
        await pause(0.3);
        await nav(page, "Export folders").click();
        await page.locator("#export-folders").waitFor();
        await pause(0.6);
        await mark();
        await until("workflows deliver to", 1.2);
      },
      targets: {
        practice: (page) => boxed(page.getByText("Practice folder", { exact: true }).first()),
        dropbox: (page) => page.getByRole("region", { name: "Dropbox and other folders" }).or(page.locator("section[aria-label='Dropbox and other folders']")).first(),
      },
    },
    "5.1": {
      async act(page, { until, mark, pause }) {
        await pause(0.3);
        await nav(page, "Updates").click();
        await pause(0.8);
        await mark("updates");
        await until("Storage shows", -0.5);
        await nav(page, "Storage").click();
        await pause(0.8);
        await mark("storage");
        await until("deleted automatically", 1.2);
      },
      targets: {
        updates: (page) => byHeading(page, "Updates"),
        storage: (page) => byHeading(page, "Storage"),
      },
    },
    "5.2": {
      async act(page, { until, mark, pause }) {
        await pause(0.3);
        await nav(page, "About").click();
        await page.locator("#diagnostics").waitFor();
        await pause(0.6);
        await mark();
        await until("conversations", 1.2);
      },
      targets: { diagnostics: (page) => page.locator("#diagnostics") },
    },
  },
};
