// Getting started (09-getting-started.md): the website shots. The terminal
// shots come from a real, recorded installer run (see animation/09-...html).

const mac = (page) => page.locator("article").filter({ has: page.locator("h2", { hasText: "Install on Mac" }) });
const ready = (page) => page.locator("#ready-title").locator("xpath=ancestor::section[1]").or(page.getByRole("heading", { name: /A few things to have ready/ }).locator("xpath=..")).first();

export default {
  site: "https://datalab.cap-study.com/",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE);
        await page.locator("h2", { hasText: "Install on Mac" }).waitFor();
        await pause(1);
      },
      async act(page, { until, mark, pause }) {
        await until("Have these ready", -0.8);
        await page.getByRole("heading", { name: /A few things to have ready/ }).evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(1);
        await mark("ready");
        await until("shared repositories", 1);
        await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
        await pause(0.8);
      },
      // The whole "have ready" card, not just its heading.
      targets: { ready: (page) => page.locator("aside.ready, aside#ready").or(page.locator("#ready-title").locator("xpath=ancestor::aside[1]")).first() },
    },
    "3.1": {
      async act(page, { until, mark, pause }) {
        await mark("download");
        await until("press Download for Mac", 0.3);
        await page.getByRole("link", { name: /Download for Mac/ }).hover();
        await until("Download for Mac", 1.2);
      },
      targets: {
        download: (page) => page.getByRole("link", { name: /Download for Mac/ }),
      },
    },
    "3.3": {
      async act(page, { until, mark, pause }) {
        await mark("command", "copy");
        await until("with the Copy button", -0.3);
        const copy = page.getByRole("button", { name: "Copy Mac install command" });
        await copy.hover();
        await pause(0.4);
        await copy.click().catch(() => {});
        await pause(0.6);
        await mark("copy");
        await until("Command and Space", -1.5);
        await mac(page).getByText("Where do I find Terminal?").click();
        await pause(0.6);
        await until("Command and Space", 1.2);
      },
      targets: {
        command: (page) => page.locator("#mac-install-command"),
        copy: (page) => page.getByRole("button", { name: "Copy Mac install command" }),
      },
    },
  },
};
