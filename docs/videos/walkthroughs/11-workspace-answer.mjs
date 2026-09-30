// Workspace part 2 (11-workspace-answer.md), on part 1's conversation.

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SAVED = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../build/10-workspace-ask/conversation.json");
const main = (page) => page.locator("main");
const panel = (page) => page.getByRole("complementary", { name: "Files, inputs, queries and history" });
const label = (page) => main(page).getByText("Answer", { exact: true }).last();
const checks = (page) => main(page).getByText(/Checks on this answer/i).last().locator("xpath=..");
const number = (page) => main(page).getByRole("button").filter({ hasText: /^[\d,.−-]+%?$/ }).first();
const made = (page) => main(page).getByRole("button", { name: /^\+?How this answer was made/ }).last();
const busy = (page, id) => page.evaluate(async (id) => (await fetch("/api/conversations").then((r) => r.json())).find((c) => c.id === id)?.busy ?? false, id);

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        const { id } = JSON.parse(readFileSync(SAVED, "utf8"));
        await page.goto(`${BASE}/workspace/${id}`);
        while (await busy(page, id)) await pause(5);
        await page.reload();
        await label(page).waitFor();
        await label(page).evaluate((n) => n.scrollIntoView({ block: "start" }));
        await page.mouse.wheel(0, -60);
        await pause(1);
      },
      async act(page, { until, mark, pause }) {
        await mark("answer");
        await until("Checks on this answer", -0.8);
        await checks(page).evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.9);
        await mark("checks");
        await until("its own work", 1);
      },
      targets: { answer: (page) => label(page).locator("xpath=.."), checks },
    },
    "2.2": {
      async act(page, { until, mark, pause }) {
        await number(page).evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark("number");
        await until("Click one", 0);
        await number(page).click();
        await pause(0.6);
        await mark("popover");
        await until("that produced it", 1.2);
        await page.keyboard.press("Escape");
      },
      targets: { number, popover: (page) => page.locator("[role=dialog]").first() },
    },
    "2.3": {
      async act(page, { until, mark, pause }) {
        await made(page).evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark("made");
        await until("Open it", -0.2);
        await made(page).click();
        await pause(0.8);
        await mark("made");
        await until("the whole story", 1);
      },
      targets: { made },
    },
    "3.1": {
      async act(page, { until, mark, pause }) {
        await page.getByRole("tab", { name: "Outputs" }).click();
        await pause(0.6);
        await mark("panel");
        await until("open and preview", 1);
      },
      targets: { panel },
    },
    "3.2": {
      async act(page, { until, mark, pause }) {
        await page.getByRole("tab", { name: "Queries" }).click();
        await pause(0.6);
        await mark("panel", "workflow");
        await until("History keeps", -0.3);
        await page.getByRole("tab", { name: "History" }).click();
        await pause(0.6);
        await mark("panel");
        await until("as they were", 1);
      },
      targets: { panel, workflow: (page) => panel(page).getByRole("button", { name: "Turn this into a workflow" }) },
    },
    "4.1": {
      async act(page, { until, mark, pause }) {
        const exp = main(page).getByRole("button", { name: "Export", exact: true }).first();
        await mark("export");
        await until("Press Export", 0.1);
        await exp.click();
        await page.getByRole("dialog").waitFor();
        await pause(0.8);
        await mark("dialog");
        await until("one web page", 1.2);
      },
      targets: {
        export: (page) => main(page).getByRole("button", { name: "Export", exact: true }).first(),
        dialog: (page) => page.getByRole("dialog"),
      },
    },
  },
};
