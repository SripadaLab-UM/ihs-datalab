// Workspace part 1 (10-workspace-ask.md): a live Analysis conversation, from
// New conversation to the plan's approval and the pilot's answer. The
// conversation is kept for part 2 (build/10-workspace-ask/conversation.json).

import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const QUESTION = "Did daily steps change over the intern year for the 2025 interns? I'd like a figure for a lab meeting.";
const SAVED = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../build/10-workspace-ask/conversation.json");

const dialog = (page) => page.getByRole("dialog");
const composer = (page) => page.locator("main").getByRole("textbox", { name: "Your question or instruction" });
const approve = (page) => page.getByRole("button", { name: "Approve plan" });
const card = (page) => page.locator("main div").filter({ has: page.getByText("Analysis plan", { exact: true }) }).filter({ has: page.getByRole("button", { name: /Approve plan|A different kind/ }) }).last();
const step = (page) => page.locator("main").getByText(/^(Queried|Read what's in|Searched the catalog|Looked up)/).first();
const answer = (page) => page.locator("main").getByText("Answer", { exact: true }).last().locator("xpath=ancestor::div[.//p][2]");
const busy = (page, id) => page.evaluate(async (id) => (await fetch("/api/conversations").then((r) => r.json())).find((c) => c.id === id)?.busy ?? false, id);

let id = null;

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE + "/workspace");
        await page.getByRole("button", { name: "New conversation" }).first().waitFor();
        await pause(1);
      },
      async act(page, { until, mark, pause }) {
        await pause(0.3);
        await page.getByRole("button", { name: "New conversation" }).first().click();
        await dialog(page).waitFor();
        await pause(0.5);
        await mark("modes");
        await until("Analysis is", -0.2);
        const analysis = dialog(page).getByRole("button", { name: /^Analysis/ });
        await analysis.hover();
        await pause(0.4);
        await analysis.click();
        await mark("note");
        await until("press Start", -0.3);
        await dialog(page).getByRole("button", { name: /^Start/ }).hover();
        await mark("start");
        await pause(0.4);
        await dialog(page).getByRole("button", { name: /^Start/ }).click();
        await page.waitForURL(/\/workspace\/c_/);
        id = page.url().split("/").at(-1);
        writeFileSync(SAVED, JSON.stringify({ id, question: QUESTION }, null, 2));
        await until("Then press Start", 2);
      },
      targets: {
        modes: (page) => dialog(page).getByRole("button", { name: /^Analysis/ }).locator("xpath=.."),
        note: (page) => dialog(page).getByText(/Data sessions can query/),
        start: (page) => dialog(page).getByRole("button", { name: /^Start/ }),
      },
    },
    "3.1": {
      async act(page, { until, mark, pause }) {
        await composer(page).click();
        await page.keyboard.type(QUESTION, { delay: 30 });
        await mark("composer");
        await pause(0.5);
        await page.keyboard.press("Enter");
        await until("finds them", 1);
      },
      targets: { composer },
    },
    "4.1": {
      async act(page, { until, mark, pause, fastForward }) {
        // The agent reads guides, looks up tables and queries, then proposes
        // its plan and waits: sped up until the narration says "Press plus".
        await fastForward(async () => {
          await approve(page).waitFor({ timeout: 600_000 });
          await pause(1);
        }, "Press plus");
        const first = step(page);
        await first.evaluate((n) => n.scrollIntoView({ block: "center" }));
        await mark("live", "step");
        await until("Press plus", 0.3);
        await first.click();
        await pause(0.8);
        await mark("step");
        await until("the queries it ran", 1.5);
      },
      targets: {
        live: (page) => step(page).locator("xpath=ancestor::*[self::ol or self::ul][1]"),
        step: (page) => step(page).locator("xpath=ancestor::li[1]"),
      },
    },
    "5.1": {
      async act(page, { until, mark, pause }) {
        await card(page).evaluate((n) => n.scrollIntoView({ block: "start" }));
        await pause(0.6);
        await mark("card", "kind");
        await until("Edit any section", -0.2);
        const field = card(page).locator("textarea").nth(1);
        await field.evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await field.click();
        await page.keyboard.press("Meta+ArrowDown");
        await page.keyboard.type(" The figure is for a lab meeting.", { delay: 40 });
        await mark("field");
        await until("or add one", 1);
      },
      targets: {
        card,
        kind: (page) => card(page).locator("textarea").first(),
        field: (page) => card(page).locator("textarea").nth(1),
      },
    },
    "5.2": {
      async act(page, { until, mark, pause }) {
        await approve(page).evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark("approve");
        await until("press Approve plan", 0.2);
        await approve(page).hover();
        await pause(0.3);
        await approve(page).click();
        await page.locator("span[title^='sha256']").first().waitFor();
        await page.locator("span[title^='sha256']").first().evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark("frozen");
        await until("or as exploratory", 1);
      },
      targets: {
        approve,
        frozen: (page) => page.locator("span[title^='sha256']").first().locator("xpath=ancestor::div[2]"),
      },
    },
    "5.3": {
      async act(page, { until, mark, pause, fastForward }) {
        await fastForward(async () => {
          await page.locator("main").getByText("Answer", { exact: true }).last().waitFor({ timeout: 900_000 });
          // Wait for the answer's text to finish arriving.
          await page.waitForFunction(() => !/Thinking|Working on it/.test(document.querySelector("main")?.innerText.slice(-600) ?? ""), null, { timeout: 300_000 }).catch(() => {});
          await pause(1.5);
        }, "It reports what it found");
        const label = page.locator("main").getByText("Answer", { exact: true }).last();
        await label.evaluate((n) => n.scrollIntoView({ block: "start" }));
        await pause(0.6);
        await mark("answer");
        await until("whether to go ahead", 1.5);
      },
      targets: {
        live: (page) => page.locator("main").getByRole("button", { name: "Stop" }).first().locator("xpath=ancestor::div[2]"),
        answer,
      },
    },
  },
  busy,
};
