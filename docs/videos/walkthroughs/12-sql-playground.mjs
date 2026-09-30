// The SQL Playground walkthrough (12-sql-playground.md): what each shot does
// on screen, and the parts of the screen its narration points at. It leads
// with the SQL assistant: ask in plain words, check and run, refine, export.

const ASK = "The number of 2025 participants with Fitbit data, and their average daily steps, by month, for July 2025 to June 2026, with the dates in the query itself.";
const REFINE = "Show it by week instead of by month.";

const editor = (page) => page.locator(".cm-editor");
const content = (page) => page.locator(".cm-content");
const tablesPanel = (page) => page.getByRole("complementary", { name: "Tables and history" });
const chat = (page) => page.getByRole("complementary", { name: "SQL drafting chat" });
const ask = (page) => chat(page).getByRole("textbox", { name: "Your question or instruction" });
const send = (page) => chat(page).getByRole("button", { name: /^(Generate SQL|Send)$/ });
const check = (page) => page.getByText(/Passes the SQL check|Only SELECT queries|SQL check/).first();
const run = (page) => page.getByRole("button", { name: /^Run/ });
const editorText = (page) => content(page).innerText();

async function clearEditor(page) {
  await content(page).click();
  await page.keyboard.press("Meta+a");
  await page.keyboard.press("Backspace");
}

/** Sends a request, and waits (sped up in the video) for the assistant to
    put a new query that passes the check into the editor. */
async function askAssistant(page, words, { fastForward, pause }, by) {
  const before = await editorText(page);
  await ask(page).click();
  await page.keyboard.type(words, { delay: 26 });
  await pause(0.4);
  await send(page).click();
  await fastForward(async () => {
    await page.waitForFunction((before) => {
      const text = document.querySelector(".cm-content")?.innerText ?? "";
      return text !== before && /select/i.test(text);
    }, before, { timeout: 300_000 });
    await page.getByText(/Passes the SQL check/).first().waitFor({ timeout: 60_000 });
    await pause(1.5);
  }, by);
}

async function runQuery(page, pause) {
  await run(page).hover();
  await pause(0.3);
  await run(page).click();
  // If the query still has bind variables, DataLab asks for their values.
  const binds = page.getByRole("dialog").filter({ hasText: /bind variables/i });
  const done = page.getByText(/\d+ rows? · \d+ ms/).first();
  await Promise.race([done.waitFor({ timeout: 120_000 }), binds.waitFor({ timeout: 120_000 })]);
  if (await binds.isVisible()) {
    for (const field of await binds.getByRole("textbox").all()) {
      const label = (await field.getAttribute("aria-label")) ?? (await field.getAttribute("name")) ?? "";
      await field.fill(/end|to/i.test(label) ? "2026-06-30" : "2025-07-01");
    }
    await binds.getByRole("button", { name: /^Run/ }).click();
    await done.waitFor({ timeout: 120_000 });
  }
  await pause(0.5);
}

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE + "/sql");
        await page.getByPlaceholder("Search tables and columns").waitFor();
        // A fresh assistant chat, and an empty editor.
        await chat(page).getByRole("button", { name: "New chat" }).click().catch(() => {});
        await clearEditor(page);
        await page.mouse.move(1900, 1070);
        await pause(1);
      },
      async act(page, { until }) {
        await until("is on the left", 1.2);
      },
      targets: { assistant: chat, editor, tables: tablesPanel },
    },
    "2.2": {
      async act(page, { until, mark, pause, fastForward }) {
        await until("Let's ask", -0.2);
        await askAssistant(page, ASK, { fastForward, pause }, "writes a query");
        await mark();
        await until("you can read", 1.5);
      },
      targets: { ask, editor },
    },
    "3.1": {
      async act(page, { until, mark }) {
        await mark();
        await until("never run", 1);
      },
      targets: { check },
    },
    "3.2": {
      async act(page, { until, mark, pause }) {
        await until("Press Run", -0.1);
        await runQuery(page, pause);
        await mark();
        await until("until you export it", 1.5);
      },
      targets: { run, grid: (page) => page.getByRole("region", { name: "Result rows" }), meta: (page) => page.getByText(/\d+ rows? · \d+ ms/).first() },
    },
    "4.1": {
      async act(page, { until, mark, pause, fastForward }) {
        await until("Tick Send with your message", -0.2);
        const box = chat(page).getByRole("checkbox", { name: /Send with/ });
        await box.check();
        await mark("sendwith");
        await until("say what you'd like", -0.2);
        await askAssistant(page, REFINE, { fastForward, pause }, "revises the query");
        await mark();
        await until("run it again", -0.2);
        await runQuery(page, pause);
        await mark();
        await pause(1);
      },
      targets: {
        sendwith: (page) => chat(page).getByText(/Send with/).first(),
        ask,
        editor,
        run,
      },
    },
    "4.2": {
      async act(page, { until, mark, pause }) {
        await until("edit it yourself", -0.2);
        await content(page).click();
        await page.keyboard.press("Meta+End");
        // A trailing semicolon would end the query before the new line.
        if ((await editorText(page)).trimEnd().endsWith(";")) await page.keyboard.press("Backspace");
        await page.keyboard.insertText("\n");
        await page.keyboard.type("FETCH FIRST 10 ROWS ONLY", { delay: 45 });
        await pause(0.8);
        await mark();
        await until("the catalog", -0.2);
        await page.getByPlaceholder("Search tables and columns").click();
        await page.keyboard.type("sleep", { delay: 90 });
        await pause(0.6);
        await mark();
        await until("into your query", 1.2);
      },
      targets: { editor, check, search: tablesPanel },
    },
    "5.1": {
      async prepare(page) {
        await page.getByPlaceholder("Search tables and columns").fill("");
      },
      async act(page, { until, mark, pause }) {
        await pause(0.3);
        await page.getByRole("tab", { name: "History" }).click();
        await pause(0.6);
        await mark("history");
        await until("Export copies", -0.4);
        await page.getByRole("button", { name: "Export", exact: true }).click();
        await page.getByRole("dialog").waitFor();
        await pause(1);
        await mark("export", "dialog");
        await until("that made it", 1.2);
      },
      targets: {
        history: tablesPanel,
        export: (page) => page.getByRole("button", { name: "Export", exact: true }),
        dialog: (page) => page.getByRole("dialog"),
      },
    },
    "5.2": {
      async prepare(page) {
        await page.keyboard.press("Escape");
        await page.getByRole("tab", { name: "Tables" }).click();
      },
      async act(page, { until, mark, pause }) {
        await until("Save as workflow", -0.4);
        await page.getByRole("button", { name: "Save as workflow" }).click();
        await pause(0.6);
        await mark();
        await until("before anything is saved", 1.5);
      },
      targets: {
        saveflow: (page) => page.getByRole("button", { name: "Save as workflow" }),
        dialog: (page) => page.getByRole("dialog"),
      },
    },
  },
};
