// Drives the practice DataLab the footage is filmed on.
//
// The app runs separately (see README.md, "Filming the app"); this signs in
// with the one-time link from its log and keeps the session in build/, so
// every filming script starts signed in. Practice profile only.

import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const BUILD = path.resolve(HERE, "../build/footage");
export const PORT = Number(process.env.FILM_PORT ?? 8780);
export const DATA_DIR = process.env.FILM_DATA_DIR ?? path.join(os.homedir(), "Library/Application Support/DataLab/showcase");
export const BASE = `http://127.0.0.1:${PORT}`;
const STATE = path.join(BUILD, `session-${PORT}.json`);
const require = createRequire(import.meta.url);

/** A signed-in page at 1440×900, drawn at 2× so crops stay sharp. mask is
    "names" (the schema's names and home-folder paths), "paths" (home-folder
    paths only), or false. With mask,
    every table name on screen is blurred (see MASK). */
export async function open({ width = 1440, height = 900, mask = "names" } = {}) {
  const health = await fetch(`${BASE}/api/health`).then((r) => r.json());
  // Footage comes from the practice profile. A real-profile DataLab may be
  // filmed only with FILM_REAL=1, on its own empty data folder, and only on
  // screens that hold no study data (checked on every navigation below).
  const real = health.profile !== "practice";
  if (real && process.env.FILM_REAL !== "1") throw new Error("Footage is filmed on the practice profile only (FILM_REAL=1 for Settings/Help on a real one).");
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width, height }, deviceScaleFactor: 2, colorScheme: "light",
    ...(existsSync(STATE) ? { storageState: STATE } : {}),
  });
  if (mask) await context.addInitScript(MASK, mask === "paths" ? { tables: [], columns: [] } : schemaNames());
  const page = await context.newPage();
  if (real) {
    const allowed = /^\/(?:workspace\/?|settings(?:\/[\w-]+)?|help(?:\/[\w-]+)?|sign-in|signed-out)?$/;
    page.on("framenavigated", (frame) => {
      if (frame !== page.mainFrame()) return;
      const where = new URL(frame.url()).pathname;
      if (!allowed.test(where)) {
        console.error(`Stopped: ${where} isn't allowed when filming a real DataLab.`);
        process.exit(3);
      }
    });
  }
  await page.goto(BASE);
  if ((await page.evaluate(() => fetch("/api/conversations").then((r) => r.status))) === 401) {
    // A sign-in link can be passed in (FILM_SIGN_IN); otherwise it's read
    // from the server log of a DataLab this folder started.
    const link = process.env.FILM_SIGN_IN?.replace(/^https?:\/\/[^/]+/, "")
      ?? readFileSync(path.join(DATA_DIR, "server.log"), "utf8").match(/\/sign-in\?token=[\w-]+/g)?.at(-1);
    if (!link) throw new Error("No sign-in link in the server log.");
    await page.goto(BASE + link);
    await context.storageState({ path: STATE });
  }
  return { browser, context, page };
}

/** Films the page until stop() is called; writes build/footage/<name>.mp4 at
    30 fps, keeping real time (Chrome sends frames only when something moves,
    so each frame is held until the next one). */
export async function record(page, name, { out: outDir = BUILD } = {}) {
  const { appendFileSync, mkdirSync, writeFileSync, rmSync } = await import("node:fs");
  const { spawnSync } = await import("node:child_process");
  const dir = path.join(outDir, "frames", name); // per video: two runs never share frames
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
    const file = path.join(dir, `${String(frames.length).padStart(5, "0")}.jpg`);
    writeFileSync(file, Buffer.from(data, "base64"));
    frames.push({ file, at: metadata.timestamp });
    appendFileSync(path.join(dir, "frames.jsonl"), JSON.stringify(frames.at(-1)) + "\n");
    cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  const { width, height } = page.viewportSize();
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: width * 2, maxHeight: height * 2 });
  const began = Date.now() / 1000;
  return async function stop(tail = 0.5) {
    await page.waitForTimeout(tail * 1000);
    await cdp.send("Page.stopScreencast");
    const ended = Date.now() / 1000;
    if (!frames.length) throw new Error(`No frames for ${name}`);
    // The concat list: each frame held until the next; the first from the start.
    const lines = frames.map((f, i) => {
      const next = i + 1 < frames.length ? frames[i + 1].at : ended;
      const from = i === 0 ? began : f.at;
      return `file '${f.file}'\nduration ${Math.max(0.001, next - from).toFixed(4)}`;
    });
    lines.push(`file '${frames.at(-1).file}'`);
    const list = path.join(dir, "list.txt");
    writeFileSync(list, lines.join("\n") + "\n");
    mkdirSync(outDir, { recursive: true });
    const out = path.join(outDir, `${name}.mp4`);
    const run = spawnSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list,
      "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", "16", "-g", "10", "-pix_fmt", "yuv420p", out]);
    if (run.status) throw new Error(run.stderr.toString());
    return { out, seconds: ended - began, frames: frames.length };
  };
}

/** The catalog's table, view and column names. The synthetic schema reuses
    the real ones, which are internal, so they're blurred in public footage. */
function schemaNames() {
  const { readdirSync } = require("node:fs");
  const root = path.join(DATA_DIR, "catalog");
  const tables = [], columns = new Set();
  for (const schema of readdirSync(root)) {
    for (const file of readdirSync(path.join(root, schema)).filter((f) => f.endsWith(".yml"))) {
      tables.push(file.slice(0, -4));
      const yml = readFileSync(path.join(root, schema, file), "utf8");
      for (const m of yml.matchAll(/^- name: ([A-Z][A-Z0-9_]{2,})$/gm)) columns.add(m[1]);
    }
  }
  return { tables, columns: [...columns] };
}

/** Runs in the page: a fixed layer of blurred patches over every table name
    (bare, or schema-qualified), role name and home-folder path, redrawn each
    frame so they follow scrolling.
    It never touches the app's own DOM. */
function MASK({ tables, columns }) {
  const any = (list) => list.sort((a, b) => b.length - a.length).join("|");
  // Tables, long or compound column names, role names and home-folder paths,
  // in any case; short column names (STEPS, VALUE) only in capitals, so the
  // same word in a sentence stays readable.
  const long = columns.filter((c) => c.length >= 10 || c.includes("_"));
  const short = columns.filter((c) => !long.includes(c));
  // With no names (mask "paths"), only home-folder paths are blurred.
  const loose = tables.length
    ? `\\b(?:IHS_\\d{4}\\.)?(?:${any([...tables, ...long])})\\b|\\bIHS_\\d{4}(?:_[A-Z]+)?\\b|/Users/\\S+|[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+`
    : "/Users/\\S+|[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+|\\b[\\w-]+(?:\\.[\\w-]+)*\\.(?:MED\\.)?UMICH\\.EDU\\b|\\bIHSP\\.WORLD\\b|\\bSVC_\\w+|\\bIHS_\\d{4}(?:_[A-Z]+)?\\b|\\bpractice\\b[^|\\n]{0,40}?(?:data\\b|DataLab\\b|folder\\b|exports\\b)?|Practice DataLab[^.\\n]*\\.?|Practice exports|Practice folder|Practice \\(synthetic data only\\)";
  const strict = short.length ? `\\b(?:${any(short)})\\b` : null;
  const matches = (text) => [...text.matchAll(new RegExp(loose, "gi")), ...(strict ? text.matchAll(new RegExp(strict, "g")) : [])];
  let layer, hits = [];
  const find = () => {
    hits = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (layer?.contains(n)) continue;
      for (const m of matches(n.data)) hits.push([n, m.index, m.index + m[0].length]);
    }
    // Form fields hold their text outside the page's text: cover the field.
    for (const f of document.querySelectorAll("textarea, input, select")) {
      const text = f.tagName === "SELECT" ? f.selectedOptions[0]?.text ?? "" : f.value;
      if (matches(text).length) hits.push([f]);
    }
  };
  // A patch only where its text is actually visible: not scrolled under a
  // sticky header, clipped away, or covered by a dialog.
  const seen = (r, owner) => {
    if (!r.width || !r.height) return false;
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!top && (owner === top || owner.contains(top) || top.contains(owner));
  };
  const paint = () => {
    const rects = hits.flatMap(([n, a, b]) => {
      if (!n.isConnected) return [];
      if (a === undefined) return [n.getBoundingClientRect()].filter((r) => seen(r, n));
      const r = document.createRange();
      r.setStart(n, a); r.setEnd(n, b);
      return [...r.getClientRects()].filter((rect) => seen(rect, n.parentElement));
    });
    while (layer.children.length < rects.length) layer.appendChild(document.createElement("div"));
    [...layer.children].forEach((d, i) => {
      const r = rects[i];
      d.style.cssText = r
        ? `position:fixed;left:${r.left - 2}px;top:${r.top - 1}px;width:${r.width + 4}px;height:${r.height + 2}px;backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);background:rgba(246,245,242,.35);border-radius:2px;pointer-events:none`
        : "display:none";
    });
    requestAnimationFrame(paint);
  };
  addEventListener("DOMContentLoaded", () => {
    layer = document.createElement("div");
    layer.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:none";
    document.documentElement.appendChild(layer);
    find();
    setInterval(find, 200);
    requestAnimationFrame(paint);
  });
}
