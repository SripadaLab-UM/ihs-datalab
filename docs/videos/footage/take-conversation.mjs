// The live take: a real Analysis conversation on the practice profile.
// Films shot 2.1 (start it, ask the question) and 2.6 (edit and approve the
// plan), then waits for the answer and its rigor review, and records the
// conversation id for the other takes, which film the finished conversation.
//
//   node docs/videos/footage/take-conversation.mjs

import { writeFileSync } from "node:fs";
import path from "node:path";
import { BUILD, open, record } from "./app.mjs";

const QUESTION = "Did daily steps change during the intern year in the 2025 cohort?";
const MINUTES = 25;

// --plan-only: a fresh conversation just for the plan shot; it stops the
// turn once the plan is approved and leaves conversation.json alone.
const PLAN_ONLY = process.argv.includes("--plan-only");
const { browser, page } = await open();
const log = (words) => console.log(`${new Date().toLocaleTimeString()}  ${words}`);
const busy = (id) => page.evaluate(async (id) => {
  const list = await fetch("/api/conversations").then((r) => r.json());
  return list.find((c) => c.id === id)?.busy ?? false;
}, id);
const progress = () => page.screenshot({ path: path.join(BUILD, "progress.png") });

// Shot 2.1: a new Analysis conversation, and the question.
let stop = PLAN_ONLY ? async () => "not filmed" : await record(page, "2.1-ask");
await page.waitForTimeout(800);
await page.getByRole("button", { name: "New conversation" }).first().click();
const dialog = page.getByRole("dialog");
await dialog.getByRole("button", { name: /^Analysis/ }).click();
await page.waitForTimeout(1800);
await dialog.getByRole("button", { name: "Start" }).click();
await page.waitForURL(/\/workspace\/[\w-]+/);
const id = page.url().split("/").at(-1);
const composer = page.getByPlaceholder("Ask a question, or say what to do next");
await composer.click();
await page.keyboard.type(QUESTION, { delay: 45 });
await page.waitForTimeout(700);
await page.keyboard.press("Enter");
log(`2.1 filmed: ${JSON.stringify(await stop(2.5))}`);
if (!PLAN_ONLY) writeFileSync(path.join(BUILD, "conversation.json"), JSON.stringify({ id, question: QUESTION }, null, 2));

// Shot 2.6: the plan. The agent waits for it, so the take can take its time.
const approve = page.getByRole("button", { name: "Approve plan" });
const deadline = Date.now() + MINUTES * 60_000;
while (!(await approve.isVisible()) && Date.now() < deadline) {
  if (!(await busy(id)) && !(await approve.isVisible())) {
    await progress();
    throw new Error("The turn ended without a plan; see progress.png.");
  }
  await progress();
  await page.waitForTimeout(5000);
}
log("plan card is up");
const card = page.locator("div", { has: page.getByText("Analysis plan", { exact: true }) }).filter({ has: approve }).last();
await card.evaluate((node) => node.scrollIntoView({ block: "start" }));
await page.mouse.move(900, 400);
stop = await record(page, "2.6-plan");
await page.waitForTimeout(2200);
const covariates = card.locator("label", { hasText: "Covariates and adjustment" }).locator("textarea");
await covariates.evaluate((node) => node.scrollIntoView({ behavior: "smooth", block: "center" }));
await page.waitForTimeout(1200);
await covariates.click();
await page.keyboard.press("End");
await covariates.evaluate((node) => node.setSelectionRange(node.value.length, node.value.length));
await page.keyboard.type(" Descriptive only: no adjustment.", { delay: 40 });
await page.waitForTimeout(900);
await approve.evaluate((node) => node.scrollIntoView({ behavior: "smooth", block: "center" }));
await page.waitForTimeout(1200);
await approve.hover();
await page.waitForTimeout(500);
await approve.click();
await page.waitForTimeout(1500);
await page.locator("span[title^='sha256']").first().evaluate((node) => node.scrollIntoView({ behavior: "smooth", block: "center" }));
log(`2.6 filmed: ${JSON.stringify(await stop(2.5))}`);

if (PLAN_ONLY) {
  await page.getByRole("button", { name: "Stop" }).first().click().catch(() => {});
  log("plan-only: turn stopped");
  await browser.close();
  process.exit(0);
}

// The rest of the turn and the rigor review, unfilmed.
while ((await busy(id)) && Date.now() < deadline) {
  await progress();
  await page.waitForTimeout(10_000);
}
await progress();
log((await busy(id)) ? "still busy at the deadline" : "turn and review finished");
await browser.close();
