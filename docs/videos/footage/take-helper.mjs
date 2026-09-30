// Shot 2.8: the research helper's approval card, in a short conversation of
// its own. Films the card, "Send to the research helper", and the answer.
//
//   node docs/videos/footage/take-helper.mjs

import { BUILD, open, record } from "./app.mjs";
import path from "node:path";

const ASK = "Before we query anything: what are the standard PHQ-9 severity cutoffs? Please check with the research helper.";

const { browser, page } = await open();
const log = (words) => console.log(`${new Date().toLocaleTimeString()}  ${words}`);

await page.getByRole("button", { name: "New conversation" }).first().click();
const dialog = page.getByRole("dialog");
await dialog.getByRole("button", { name: /^Data extraction/ }).click();
await dialog.getByRole("button", { name: "Start" }).click();
await page.waitForURL(/\/workspace\/[\w-]+/);
await page.getByPlaceholder("Ask a question, or say what to do next").fill(ASK);
await page.keyboard.press("Enter");

const send = page.getByRole("button", { name: "Send to the research helper" });
await send.waitFor({ timeout: 10 * 60_000 });
log("approval card is up");
await send.evaluate((node) => node.scrollIntoView({ block: "center" }));
await page.mouse.move(900, 300);
const stop = await record(page, "2.8-helper");
await page.waitForTimeout(3000);
await send.hover();
await page.waitForTimeout(700);
await send.click();
await page.getByText("The helper's answer").waitFor({ timeout: 5 * 60_000 }).catch(() => {});
await page.getByText("The helper's answer").evaluate((node) => node.scrollIntoView({ behavior: "smooth", block: "center" })).catch(() => {});
log(`2.8 filmed: ${JSON.stringify(await stop(3))}`);
await page.screenshot({ path: path.join(BUILD, "progress-helper.png") });
await browser.close();
