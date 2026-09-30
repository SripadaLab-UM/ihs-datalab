// Shots 3.1–3.6, filmed on the finished conversation from take-conversation,
// one take each. Table names are blurred (see MASK in app.mjs).
//
//   node docs/videos/footage/take-finished.mjs            # every shot
//   node docs/videos/footage/take-finished.mjs 3.4 3.6    # some

import { readFileSync } from "node:fs";
import path from "node:path";
import { BASE, BUILD, open, record } from "./app.mjs";

const { id } = JSON.parse(readFileSync(path.join(BUILD, "conversation.json")));
const wanted = process.argv.slice(2);
const { browser, page } = await open();
const pause = (s) => page.waitForTimeout(s * 1000);
const glide = (locator, block = "center") =>
  locator.evaluate((node, block) => node.scrollIntoView({ behavior: "smooth", block }), block);
const tab = (name) => page.locator("aside").getByText(name, { exact: true });

async function fresh() {
  await page.goto(`${BASE}/workspace/${id}`);
  await page.getByText("Answer", { exact: true }).waitFor();
  await page.mouse.move(1000, 600);
  await pause(1);
}

const SHOTS = {
  // History: the checkpoints, and Restore.
  async "3.1"() {
    await fresh();
    const stop = await record(page, "3.1-history");
    await pause(1);
    await tab("History").click();
    await pause(2);
    const restore = page.locator("aside").getByRole("button", { name: /restore/i }).first();
    if (await restore.count()) { await restore.hover(); await pause(2); }
    return stop(1);
  },
  // The answer, then its number check.
  async "3.2"() {
    await fresh();
    await page.getByText("Answer", { exact: true }).evaluate((node) => node.scrollIntoView({ block: "start" }));
    const stop = await record(page, "3.2-answer");
    await pause(2.5);
    const chip = page.getByText(/numbers? (not )?matched to this turn's outputs/).first();
    await glide(chip);
    await pause(1.2);
    await chip.hover();
    return stop(2.5);
  },
  // The Rigor review switch, then the review under the answer.
  async "3.3"() {
    await fresh();
    const stop = await record(page, "3.3-rigor");
    await pause(1);
    await page.locator("header, [class*=sticky]").getByText("Rigor review", { exact: true }).first().hover().catch(() => {});
    await pause(2);
    const review = page.locator("main").getByRole("button", { name: /^Rigor review/ }).last();
    await glide(review);
    await pause(1);
    await review.click();
    await pause(0.6);
    await glide(review, "start");
    return stop(4);
  },
  // A step's exact SQL, then the Queries tab.
  async "3.4"() {
    await fresh();
    const made = page.locator("main").getByRole("button", { name: /^How this answer was made/ });
    await made.click();
    const step = page.locator("main").getByText(/^Queried /).first();
    await step.evaluate((node) => node.scrollIntoView({ block: "center" }));
    const stop = await record(page, "3.4-sql");
    await pause(1.5);
    await step.click();
    await pause(0.5);
    const sql = page.locator("main pre").first();
    if (await sql.count()) await glide(sql);
    await pause(3);
    await tab("Queries").click();
    await pause(3);
    return stop(1);
  },
  // Export files, then Export conversation.
  async "3.5"() {
    await fresh();
    const stop = await record(page, "3.5-export");
    await pause(1);
    await page.locator("aside").getByRole("button", { name: /Export/ }).first().click();
    await pause(3.5);
    await page.keyboard.press("Escape");
    await pause(1);
    await page.getByRole("button", { name: "Export", exact: true }).first().click();
    await pause(3.5);
    await page.keyboard.press("Escape");
    return stop(1);
  },
  // Settings: Run safety check, live.
  async "3.6"() {
    await page.goto(BASE);
    await page.getByRole("link", { name: "Settings & Safety" }).click();
    const run = page.getByRole("button", { name: "Run safety check" });
    await run.waitFor();
    await glide(run);
    await pause(1);
    const stop = await record(page, "3.6-safety");
    await pause(1.5);
    await run.click();
    await page.getByText(/Every check passed|check(s)? failed|couldn't be verified/).waitFor({ timeout: 180_000 });
    await pause(1.5);
    const verdict = page.getByText(/Every check passed|check(s)? failed|couldn't be verified/).first();
    await glide(verdict, "start");
    await pause(2);
    await page.mouse.wheel(0, 500);
    await pause(2);
    await page.mouse.wheel(0, 500);
    return stop(2);
  },
};

for (const [shot, film] of Object.entries(SHOTS)) {
  if (wanted.length && !wanted.includes(shot)) continue;
  try {
    console.log(shot, JSON.stringify(await film()));
  } catch (error) {
    await page.screenshot({ path: path.join(BUILD, `failed-${shot}.png`) });
    console.log(shot, "FAILED:", error.message.split("\n")[0]);
  }
}
await browser.close();
