// Renders a video's animation page to MP4 with its narration.
//
//   node render.mjs 01-how-datalab-works                 # the whole video
//   node render.mjs 01-how-datalab-works --stills 5,40   # PNG frames at those seconds
//   node render.mjs 01-how-datalab-works --from 44 --to 75
//   node render.mjs 01-how-datalab-works --audit         # timing checks, no video
//
// The page (animation/<name>.html) draws any moment through window.seek(t);
// this steps through it frame by frame in the installed Google Chrome and
// pipes the frames to ffmpeg. Output: build/<name>/<name>.mp4 (or stills/).

import { spawn } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../.."); // the repo: the page loads fonts from frontend/
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json",
  ".srt": "text/plain", ".wav": "audio/wav", ".woff2": "font/woff2", ".mp4": "video/mp4", ".md": "text/markdown" };

const args = process.argv.slice(2);
const name = args[0];
const option = (flag, fallback) => (args.includes(flag) ? args[args.indexOf(flag) + 1] : fallback);
// A video has its own animation page, or is a tab walkthrough: a script with
// takes (walkthroughs/<name>.mjs), drawn by the shared walkthrough page.
const own = existsSync(path.join(HERE, "animation", `${name}.html`));
const walkthrough = existsSync(path.join(HERE, "walkthroughs", `${name}.mjs`));
if (!name || !(own || walkthrough)) {
  console.error("Usage: node render.mjs <name> [--stills 1,2] [--from s] [--to s] [--fps 30]");
  process.exit(1);
}
const fps = Number(option("--fps", 30));
const build = path.join(HERE, "build", name);

// Serve the repo on 127.0.0.1 only, and nothing outside it.
const server = http.createServer((req, res) => {
  const file = path.join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!file.startsWith(ROOT + path.sep) || !existsSync(file) || !statSync(file).isFile()) {
    res.writeHead(404).end();
    return;
  }
  const type = TYPES[path.extname(file)] ?? "application/octet-stream";
  const size = statSync(file).size;
  // Byte ranges, so a <video> can seek (the walkthroughs' footage).
  const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range ?? "");
  if (range) {
    const first = range[1] ? Number(range[1]) : size - Number(range[2]);
    const last = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    res.writeHead(206, { "content-type": type, "accept-ranges": "bytes", "content-length": last - first + 1,
      "content-range": `bytes ${first}-${last}/${size}` });
    createReadStream(file, { start: first, end: last }).pipe(res);
    return;
  }
  res.writeHead(200, { "content-type": type, "accept-ranges": "bytes", "content-length": size });
  createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}/docs/videos/animation/` +
  (own ? `${name}.html` : `walkthrough.html?v=${name}`);

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (error) => { console.error(error); process.exit(1); });
await page.goto(url);
await page.waitForFunction(() => window.ready === true);
const duration = await page.evaluate(() => window.duration);

const frame = async (t, options) => {
  await page.evaluate((t) => window.seek(t), t);
  return page.screenshot(options);
};

if (args.includes("--audit")) {
  // Every 0.1 s: never two footage windows at once, and never a window whose
  // subject isn't on the map yet.
  const problems = await page.evaluate((duration) => {
    const seen = (node) => {
      let o = 1;
      for (let n = node; n && n.tagName !== "svg"; n = n.parentNode) {
        if (n.style?.display === "none") return 0;
        o *= +(n.getAttribute?.("opacity") ?? 1);
      }
      return o;
    };
    const { windows, subjects } = window.audit;
    const found = [];
    for (let t = 0; t < duration; t += 0.1) {
      window.seek(t);
      const up = windows.filter((w) => seen(w.g) > 0.05);
      if (up.length > 1) found.push(`${t.toFixed(1)} s: ${up.map((w) => w.shot).join(" and ")} on screen together`);
      for (const w of up) if (seen(subjects[w.shot]) < 0.5) found.push(`${t.toFixed(1)} s: window ${w.shot} is up before its subject`);
    }
    return found;
  }, duration);
  console.log(problems.length ? problems.join("\n") : "Audit passed: one window at a time, each after its subject.");
  if (problems.length) process.exitCode = 1;
} else if (args.includes("--stills")) {
  mkdirSync(path.join(build, "stills"), { recursive: true });
  for (const t of option("--stills").split(",").map(Number)) {
    const out = path.join(build, "stills", `t${t.toFixed(1).padStart(6, "0")}.png`);
    writeFileSync(out, await frame(t, { type: "png" }));
    console.log(out);
  }
} else {
  const from = Number(option("--from", 0));
  const to = Math.min(Number(option("--to", duration)), duration);
  const whole = from === 0 && to === duration;
  const out = path.join(build, whole ? `${name}.mp4` : `${name}-${from}-${to}.mp4`);
  const ffmpeg = spawn("ffmpeg", [
    "-y", "-loglevel", "error",
    "-f", "image2pipe", "-framerate", String(fps), "-c:v", "mjpeg", "-i", "-",
    "-ss", String(from), "-t", String(to - from), "-i", path.join(build, "narration.wav"),
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out,
  ], { stdio: ["pipe", "inherit", "inherit"] });
  const frames = Math.round((to - from) * fps);
  const began = Date.now();
  for (let i = 0; i < frames; i++) {
    const jpeg = await frame(from + i / fps, { type: "jpeg", quality: 92 });
    if (!ffmpeg.stdin.write(jpeg)) await new Promise((resolve) => ffmpeg.stdin.once("drain", resolve));
    if (i % (fps * 10) === 0) {
      const done = (i / frames) * 100;
      console.log(`${(from + i / fps).toFixed(0).padStart(4)} s  ${done.toFixed(0).padStart(3)}%  ${((Date.now() - began) / 1000).toFixed(0)} s elapsed`);
    }
  }
  ffmpeg.stdin.end();
  const code = await new Promise((resolve) => ffmpeg.on("close", resolve));
  if (code) process.exit(code);
  console.log(out);
}

await browser.close();
server.close();
