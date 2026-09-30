// The Help walkthrough (15-help.md): the guide list, a guide, search, the
// Glossary, and Help opened from a tooltip and from the SQL Playground.

const nav = (page) => page.getByRole("navigation", { name: "Help" });
const article = (page) => page.locator("main article").first();
const search = (page) => page.locator("input[type=search]");
const helpLink = (page) => page.locator("a[href^='/help']").filter({ hasText: /^Help$/ }).first();
const section = (page, name) => nav(page).locator("section").filter({ has: page.getByRole("heading", { name, exact: true }) });
const learn = (page) => page.getByRole("link", { name: "Learn more" }).filter({ visible: true }).last();

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE + "/help");
        await article(page).waitFor();
        await pause(1);
      },
      async act(page, { until, mark }) {
        await mark("helplink");
        await until("the list of guides", 0);
        await mark("list");
        await until("Working with the agent", 0);
        await mark("agent");
        await until("opens on the right", 0);
        await mark("guide");
        await until("opens on the right", 1.5);
      },
      targets: {
        helplink: helpLink,
        list: nav,
        agent: (page) => section(page, "Working with the agent"),
        guide: article,
      },
    },
    "2.2": {
      async act(page, { until, mark, pause }) {
        const item = nav(page).getByRole("link", { name: "Ask your first question" });
        await mark("item");
        await pause(0.5);
        await item.hover();
        await pause(0.3);
        await item.click();
        await page.waitForURL(/first-question/);
        await pause(0.6);
        await mark("item", "guide");
        await until("Each guide is short", 0.8);
        await page.mouse.move(1100, 600);
        await page.mouse.wheel(0, 500);
        await pause(0.8);
        await mark("guide");
        await until("watch it work", 1.2);
        await page.mouse.wheel(0, -3000);
        await pause(1);
      },
      targets: {
        item: (page) => nav(page).getByRole("link", { name: "Ask your first question" }),
        guide: article,
      },
    },
    "3.1": {
      async act(page, { until, mark, pause }) {
        await until("To find something", -0.2);
        await search(page).click();
        await pause(0.3);
        await mark("search");
        await until("like research helper", -0.6);
        await page.keyboard.type("research helper", { delay: 60 });
        await pause(0.8);
        await mark("results");
        await until("where they appear", 1.2);
      },
      targets: {
        search: (page) => search(page).locator("xpath=.."),
        results: nav,
      },
    },
    "3.2": {
      async act(page, { until, mark, pause }) {
        const res = nav(page).getByRole("link", { name: /^Research helper/ });
        await res.hover();
        await pause(0.3);
        await res.click();
        await page.waitForURL(/glossary/);
        await page.locator("#research-helper").waitFor();
        await page.locator("#research-helper").evaluate((n) => n.scrollIntoView({ block: "start" }));
        await page.mouse.wheel(0, -40);
        await pause(0.7);
        await mark("term");
        await until("its tooltip", 1.5);
      },
      targets: {
        term: (page) => page.locator("#research-helper").locator("xpath=following-sibling::p[1]"),
      },
    },
    "4.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE + "/sql");
        await page.getByRole("button", { name: "Data session" }).waitFor();
        await pause(1);
      },
      async act(page, { until, mark, pause }) {
        const ds = page.getByRole("button", { name: "Data session" });
        await until("a tooltip's", -0.8);
        await ds.hover();
        await pause(0.2);
        await ds.click();
        await pause(0.5);
        await mark("tip");
        await until("Learn more", 0);
        await learn(page).hover();
        await mark("learn");
        await until("the Help link", -0.3);
        await page.keyboard.press("Escape");
        await page.mouse.click(900, 800);
        await mark("helplink");
        await helpLink(page).hover();
        await until("the guide for", 0);
        await helpLink(page).click();
        await page.waitForURL(/\/help\/sql-playground/);
        await article(page).waitFor();
        await pause(0.5);
        await mark("guide");
        await until("the SQL Playground", 1.5);
      },
      targets: {
        tip: (page) => learn(page).locator("xpath=ancestor::span[contains(@class,'border')][1]"),
        learn,
        helplink: helpLink,
        guide: article,
      },
    },
  },
};
