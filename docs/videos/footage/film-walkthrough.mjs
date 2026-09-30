// Films a tab walkthrough, one take per shot, in one browser session, so
// each take carries on from where the last one left the screen.
//
//   node docs/videos/footage/film-walkthrough.mjs 12-sql-playground [2.3 2.4]
//
// The walkthrough's actions live in walkthroughs/<name>.mjs: for each shot,
// an optional `prepare` (not filmed), an `act` (filmed), and `targets`, the
// parts of the screen the narration points at. Where each target sits is
// measured while the take runs (take.mark(), and once more at its end), so the
// spotlight always sits on the real element.
//
// Writes build/<name>/takes/<shot>.mp4 and build/<name>/takes.json.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { BASE, HERE, open, record } from "./app.mjs";

const [name, ...only] = process.argv.slice(2);
const { default: walkthrough } = await import(pathToFileURL(path.join(HERE, "../walkthroughs", `${name}.mjs`)).href);
const dir = path.resolve(HERE, "../build", name, "takes");
const manifest = path.join(dir, "..", "takes.json");
const takes = existsSync(manifest) ? JSON.parse(readFileSync(manifest, "utf8")) : {};

// When each phrase is spoken, from the narration (make it first): so a take
// can wait for its words, and a click lands when the narrator says it.
const build = path.resolve(HERE, "../build", name);
const timing = JSON.parse(readFileSync(path.join(build, "timing.json"), "utf8"));
const captions = readFileSync(path.join(build, "captions.srt"), "utf8").trim().split(/\n\s*\n/).map((block) => {
  const [, times, ...lines] = block.split("\n");
  const [a, b] = times.split(" --> ").map((s) => s.split(/[:,]/).map(Number)).map(([h, m, sec, ms]) => h * 3600 + m * 60 + sec + ms / 1000);
  return { a, b, text: lines.join(" ") };
});
function spokenAt(shot, phrase) {
  const { start, end } = timing.shots.find((s) => s.shot === shot);
  for (const c of captions) {
    if (c.a < start - 0.01 || c.b > end + 0.01) continue;
    const i = c.text.indexOf(phrase);
    if (i >= 0) return c.a + (i / c.text.length) * (c.b - c.a) - start;
  }
  throw new Error(`"${phrase}" isn't spoken in shot ${shot}`);
}

// A walkthrough of DataLab signs in to it; one with `site` films that public
// website in a plain browser instead (shots may still name DataLab screens).
const { browser, page } = walkthrough.site
  ? await (async () => {
      const { chromium } = await import("playwright-core");
      const browser = await chromium.launch({ channel: "chrome", headless: true });
      const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2, colorScheme: "light", acceptDownloads: false });
      return { browser, page: await context.newPage() };
    })()
  : await open({ width: 1920, height: 1080, mask: walkthrough.mask ?? "paths" });
const pause = (s) => page.waitForTimeout(s * 1000);

for (const [shot, spec] of Object.entries(walkthrough.shots)) {
  if (!spec.act) continue; // a shot with no footage (the agenda, the end card)
  if (only.length && !only.includes(shot)) {
    // A shot not being re-filmed still sets up the screen for the next one.
    if (spec.replay) await spec.replay(page, { BASE, pause });
    continue;
  }
  if (spec.prepare) await spec.prepare(page, { BASE: walkthrough.site ?? BASE, pause });
  await page.mouse.move(1900, 1070);
  const began = Date.now();
  const elapsed = () => (Date.now() - began) / 1000;
  const rects = {};
  const ff = [];
  // Wait until the phrase is spoken (plus extra seconds), measured from the
  // start of the take, which starts with the shot's narration.
  const until = async (phrase, extra = 0) => {
    const wait = spokenAt(shot, phrase) + extra - elapsed();
    if (wait > 0) await pause(wait);
  };
  // A wait that isn't worth watching (the model at work): sped up in the
  // video, so that it ends by the phrase `by` (or with the shot).
  const fastForward = async (work, by) => {
    const a = elapsed();
    await work();
    ff.push([a, elapsed(), by ? spokenAt(shot, by) : null]);
  };
  const mark = async (...ids) => {
    const at = (Date.now() - began) / 1000;
    for (const id of ids.length ? ids : Object.keys(spec.targets ?? {})) {
      const box = await spec.targets[id](page).boundingBox({ timeout: 400 }).catch(() => null);
      if (box) (rects[id] ??= []).push({ at, ...box });
    }
  };
  const stop = await record(page, shot, { out: dir });
  await mark();
  await spec.act(page, { BASE: walkthrough.site ?? BASE, pause, mark, until, fastForward });
  await mark();
  const { out, seconds } = await stop(0.3);
  // A URL path for the page, with / on Windows too.
  takes[shot] = { file: path.relative(path.join(dir, ".."), out).split(path.sep).join("/"), seconds, rects, ff };
  writeFileSync(manifest, JSON.stringify(takes, null, 2));
  console.log(`${shot}: ${seconds.toFixed(1)} s, targets ${Object.keys(rects).join(", ") || "none"}`);
}
await browser.close();
