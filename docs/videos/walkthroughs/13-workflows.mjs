// The Workflows walkthrough (13-workflows.md). It leads with the Workflow
// assistant changing the open workflow, then runs it, opens its record, and
// shows Run again and Replay.

const FILE = "/workflows/file?path=builtin%2Fmood_daily_2025.yaml";
const REQUEST = "Add a check before delivery that there's only one row per participant per day.";

const list = (page) => page.getByRole("complementary", { name: "Workflow files" });
const chat = (page) => page.getByRole("complementary", { name: "Workflow authoring chat" });
const section = (page, heading) =>
  page.locator("main section").filter({ has: page.getByRole("heading", { name: heading, exact: true }) }).first();

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE + "/workflows");
        await list(page).waitFor();
        await chat(page).getByRole("button", { name: "New chat" }).click().catch(() => {});
        await pause(1);
      },
      async act(page, { until }) {
        await until("change them", 1.2);
      },
      targets: { list, chat },
    },
    "2.2": {
      async act(page, { until, mark, pause, fastForward }) {
        await pause(0.4);
        await list(page).getByText("mood_daily_2025").first().click();
        await page.waitForURL(/mood_daily_2025/);
        await until("tick Send with your message", -0.2);
        await chat(page).getByRole("checkbox", { name: /Send with/ }).check();
        await mark("sendwith");
        await until("ask for a change", -0.2);
        await chat(page).getByRole("textbox", { name: "Your question or instruction" }).click();
        await page.keyboard.type(REQUEST, { delay: 24 });
        await mark("ask");
        await pause(0.3);
        await chat(page).getByRole("button", { name: "Send", exact: true }).click();
        await fastForward(async () => {
          await chat(page).getByText(/check_workflow passed|workflow check passed|passed/i).last().waitFor({ timeout: 360_000 });
          await chat(page).getByRole("button", { name: "Send", exact: true }).waitFor({ timeout: 120_000 });
          await pause(1);
        }, "checks it with DataLab's");
        await mark("chat");
        await until("never saves", 1.5);
      },
      targets: {
        sendwith: (page) => chat(page).getByText(/Send with/).first(),
        ask: (page) => chat(page).getByRole("textbox", { name: "Your question or instruction" }),
        chat,
      },
    },
    "3.1": {
      async act(page, { until, mark, pause }) {
        await section(page, "Run").evaluate((n) => n.scrollIntoView({ block: "start" }));
        await mark("form");
        await until("press Run", -0.2);
        const run = page.locator("main form").getByRole("button", { name: "Run" });
        await run.hover();
        await pause(0.3);
        await run.click();
        await mark("run");
        await until("exactly as written", 1);
      },
      targets: {
        form: (page) => page.locator("main form").first(),
        run: (page) => page.locator("main form").getByRole("button", { name: "Run" }),
      },
    },
    "3.2": {
      async act(page, { until, mark }) {
        await section(page, "Steps").getByText("1 check passed").waitFor({ timeout: 120_000 });
        await mark();
        await until("every check passes", 1.2);
      },
      targets: { steps: (page) => section(page, "Steps") },
    },
    "4.1": {
      async act(page, { until, mark, pause }) {
        const runs = section(page, "Runs");
        await runs.evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await runs.getByRole("link").first().click();
        await page.waitForURL(/\/workflows\/runs\//);
        await pause(0.6);
        await mark();
        await until("everything it pinned", -0.4);
        await section(page, "What this run pinned").evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark();
        await until("the data it extracted", 1.2);
      },
      targets: {
        delivery: (page) => section(page, "Delivery"),
        pinned: (page) => section(page, "What this run pinned"),
      },
    },
    "5.1": {
      async act(page, { until, mark, pause }) {
        await section(page, "Repeat this run").evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark("again", "replay");
        await until("reproduce its results", -1.2);
        await page.getByRole("button", { name: /^Replay/ }).click();
        await page.getByRole("dialog").waitFor({ timeout: 10_000 }).catch(() => {});
        await pause(0.6);
        await mark("dialog");
        await until("exactly", 1.5);
      },
      targets: {
        again: (page) => page.getByRole("button", { name: "Run again" }),
        replay: (page) => page.getByRole("button", { name: /^Replay/ }),
        dialog: (page) => page.getByRole("dialog"),
      },
    },
  },
};
