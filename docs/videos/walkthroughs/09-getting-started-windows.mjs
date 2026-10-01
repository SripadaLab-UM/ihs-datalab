// Getting started on Windows (09-getting-started-windows.md): the website
// shots. The desktop shots come from footage/record-windows-setup.ps1.
//
// The Michigan Medicine profile page is never opened: the takes only point
// at the website's link to it.

export default {
  site: "https://datalab.cap-study.com/",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE);
        await page.locator("h2", { hasText: "Install on Windows" }).waitFor();
        await pause(1);
      },
      async act(page, { until, mark, pause }) {
        await until("Have these ready", -0.8);
        await page.getByRole("heading", { name: /A few things to have ready/ }).evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(1);
        await mark("ready", "privileged");
        await until("shared repositories", 1);
        await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
        await pause(0.8);
      },
      targets: {
        // The whole "have ready" card, not just its heading.
        ready: (page) => page.locator("aside.ready, aside#ready").or(page.locator("#ready-title").locator("xpath=ancestor::aside[1]")).first(),
        privileged: (page) => page.locator("aside.ready").getByRole("link", { name: /privileged access/ }),
      },
    },
    "3.1": {
      async act(page, { until, mark }) {
        await mark("download");
        await until("press Download for Windows", 0.3);
        await page.getByRole("link", { name: /Download for Windows/ }).hover();
        await until("Download for Windows", 1.2);
      },
      targets: {
        download: (page) => page.getByRole("link", { name: /Download for Windows/ }),
      },
    },
    "3.3": {
      async act(page, { until, mark, pause }) {
        await mark("command", "copy");
        await until("with the Copy button", -0.3);
        const copy = page.getByRole("button", { name: "Copy Windows install command" });
        await copy.hover();
        await pause(0.4);
        await copy.click().catch(() => {});
        await pause(0.6);
        await mark("copy");
        await until("type PowerShell", 1.2);
      },
      targets: {
        command: (page) => page.locator("#windows-install-command"),
        copy: (page) => page.getByRole("button", { name: "Copy Windows install command" }),
      },
    },
    // DataLab's first screen, on the real profile of the PC that was just
    // set up (no conversations yet; no VPN, so the database isn't tested).
    // Nothing is hovered: the status buttons' tooltips name the accounts.
    //   FILM_REAL=1 FILM_PORT=8765 FILM_DATA_DIR=<its data folder> FILM_SIGN_IN=<its sign-in link>
    "5.3": {
      app: true,
      async prepare(page, { BASE, pause }) {
        await page.goto(`${BASE}/workspace`);
        await page.getByRole("button", { name: /^Database:/ }).waitFor();
        await pause(1.5);
      },
      async act(page, { until, mark }) {
        await mark();
        await until("Getting connected", 1.2);
      },
      targets: {
        workspace: (page) => page.locator("main"),
        // The four status buttons (database, key, GitHub, export folders).
        status: (page) => page.getByRole("button", { name: /^Database:/ }).locator("xpath=../.."),
      },
    },
  },
};
