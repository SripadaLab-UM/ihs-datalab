// A small animation kit for the videos. Every frame is a pure function of the
// time t (seconds), so a render is repeatable and any moment can be previewed.

export const NS = "http://www.w3.org/2000/svg";

// The paper palette (frontend/src/styles/index.css), light theme.
export const C = {
  paper: "#ffffff",
  sunken: "#f6f5f2",
  line: "#e7e5df",
  ink: "#2b2a26",
  muted: "#6c6a62",
  faint: "#767369",
  accentSoft: "#f1efea",
  data: "#2f6b4f",
  dataSoft: "#edf3ef",
  research: "#8a5a12",
  researchSoft: "#f7f0e3",
  you: "#45517a",
  youSoft: "#eef0f6",
  danger: "#9b2c1f",
  dangerSoft: "#f9ece9",
};

export const FONT = {
  serif: '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif',
  sans: '"Schibsted Grotesk Variable", "Helvetica Neue", Arial, sans-serif',
  mono: '"IBM Plex Mono", Menlo, monospace',
};

// ---- Timing: the narration's timing.json and captions.srt -----------------

export async function loadTiming(base) {
  const [timing, srt] = await Promise.all([
    fetch(`${base}/timing.json`).then((r) => r.json()),
    fetch(`${base}/captions.srt`).then((r) => r.text()),
  ]);
  const captions = srt
    .trim()
    .split(/\n\s*\n/)
    .map((block) => {
      const [, times, ...lines] = block.split("\n");
      const [start, end] = times.split(" --> ").map(seconds);
      return { start, end, text: lines.join(" ") };
    });
  return { ...timing, captions, shot: Object.fromEntries(timing.shots.map((s) => [s.shot, s])) };
}

function seconds(stamp) {
  const [h, m, rest] = stamp.split(":");
  const [s, ms] = rest.split(",");
  return +h * 3600 + +m * 60 + +s + +ms / 1000;
}

/** When a phrase is spoken: its caption's start plus its share of that caption. */
export function spoken(T, shotId, phrase, offset = 0) {
  const shot = T.shot[shotId];
  for (const c of T.captions) {
    if (c.start < shot.start - 0.01 || c.end > shot.end + 0.01) continue;
    const i = c.text.indexOf(phrase);
    if (i >= 0) return c.start + (i / c.text.length) * (c.end - c.start) + offset;
  }
  throw new Error(`"${phrase}" isn't spoken in shot ${shotId}`);
}

// ---- Easing ---------------------------------------------------------------

export const clamp = (x, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, x));
export const lerp = (a, b, p) => a + (b - a) * p;
export const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2);
/** 0 before t0, easing to 1 over d seconds. */
export const ramp = (t, t0, d = 0.6) => ease(clamp((t - t0) / d));
/** Fades in at t0 and out at t1. */
export const during = (t, t0, t1, d = 0.5) => Math.min(ramp(t, t0, d), 1 - ramp(t, t1, d));

// ---- Drawing --------------------------------------------------------------

export function el(tag, attrs = {}, parent = null, content = null) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (content !== null) node.textContent = content;
  if (parent) parent.appendChild(node);
  return node;
}

export function text(parent, x, y, content, o = {}) {
  const label = o.label;
  return el(
    "text",
    {
      x,
      y,
      "font-family": FONT[label ? "sans" : o.font ?? "serif"],
      "font-size": label ? 13 : o.size ?? 22,
      "font-weight": label ? 600 : o.weight ?? 400,
      "font-style": o.italic ? "italic" : "normal",
      "letter-spacing": label ? "0.14em" : o.spacing ?? 0,
      fill: o.fill ?? (label ? C.faint : C.ink),
      "text-anchor": o.anchor ?? "start",
    },
    parent,
    label ? content.toUpperCase() : content,
  );
}

/** Show or hide a wrapper group: p is 0..1, with a small rise as it arrives. */
export function show(g, p, rise = 8) {
  g.style.display = p <= 0.001 ? "none" : "";
  g.setAttribute("opacity", p.toFixed(3));
  g.setAttribute("transform", `translate(0 ${((1 - p) * rise).toFixed(2)})`);
}

/** Draw a stroke on, 0..1 (the element needs pathLength="1"). */
export function draw(node, p) {
  node.style.display = p <= 0.001 ? "none" : "";
  node.setAttribute("stroke-dasharray", "1 1");
  node.setAttribute("stroke-dashoffset", (1 - p).toFixed(4));
}

// ---- Icons (centred on 0,0) ------------------------------------------------

const stroke = (color, width = 2) => ({ fill: "none", stroke: color, "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round" });

export const ICON = {
  key(g, color = C.ink) {
    el("circle", { cx: -9, cy: 0, r: 6.5, ...stroke(color) }, g);
    el("path", { d: "M-2.5 0 H13 M8 0 v5 M12 0 v4", ...stroke(color) }, g);
  },
  lock(g, color = C.ink) {
    el("path", { d: "M-6 -2 v-5 a6 6 0 0 1 12 0 v5", ...stroke(color) }, g);
    el("rect", { x: -9, y: -2, width: 18, height: 14, rx: 2, fill: color }, g);
  },
  folder(g, color = C.ink) {
    el("path", { d: "M-24 -15 h15 l5 6 h28 v26 h-48 z", ...stroke(color, 1.5), fill: C.paper }, g);
  },
  file(g, color = C.ink) {
    el("path", { d: "M-9 -12 h11 l7 7 v17 h-18 z M2 -12 v7 h7", ...stroke(color, 1.75), fill: C.paper }, g);
  },
  table(g, color = C.ink) {
    el("rect", { x: -13, y: -10, width: 26, height: 20, rx: 1.5, ...stroke(color, 1.75), fill: C.paper }, g);
    el("path", { d: "M-13 -3 H13 M-13 4 H13 M-4 -10 V10", ...stroke(color, 1.25) }, g);
  },
  tick(g, color = C.data) {
    el("path", { d: "M-8 0 l5 5 l11 -12", ...stroke(color, 2.75) }, g);
  },
  cross(g, color = C.danger) {
    el("path", { d: "M-7 -7 L7 7 M7 -7 L-7 7", ...stroke(color, 3) }, g);
  },
  card(g, color = C.ink) {
    el("rect", { x: -14, y: -10, width: 28, height: 20, rx: 2, ...stroke(color, 1.75), fill: C.paper }, g);
    el("path", { d: "M-8 -3 H8 M-8 3 H4", ...stroke(color, 1.25) }, g);
  },
};

export function icon(parent, name, x, y, color, scale = 1) {
  const g = el("g", { transform: `translate(${x} ${y}) scale(${scale})` }, parent);
  ICON[name](g, color);
  return g;
}

// ---- Footage windows: a crop of a walkthrough's take, played in a box ------

const takesCache = {};
const loadTakes = (name) => (takesCache[name] ??= fetch(`../build/${name}/takes.json`).then((r) => r.json()));

/** A window at box {x, y, w, h} that plays walkthrough `v`'s take for `shot`,
    cropped around the spotlight target `target` as measured at take time
    `at` (default: its first measure), or around `rect` ({x, y, width,
    height}), from take time `from`. Returns
    { g, seek(local) }, where local is seconds since the window opened. */
export async function clip(parent, box, { v, shot, target, at, from = 0, pad = 40, speed = 1, rect }) {
  const take = (await loadTakes(v))[shot];
  if (!take) throw new Error(`No take ${v} ${shot}`);
  const marks = take.rects[target] ?? [];
  const m = rect ?? (at == null ? marks[0] : marks.findLast((r) => r.at <= at + 0.05) ?? marks[0]) ?? { x: 0, y: 0, width: 1920, height: 1080 };
  // The crop: the target plus padding, widened to the box's shape, kept on screen.
  const ratio = box.w / box.h;
  let w = Math.max(m.width + 2 * pad, (m.height + 2 * pad) * ratio);
  w = Math.min(w, 1920, 1080 * ratio);
  const h = w / ratio;
  const cx = Math.min(Math.max(m.x + m.width / 2 - w / 2, 0), 1920 - w);
  const cy = Math.min(Math.max(m.y + m.height / 2 - h / 2, 0), 1080 - h);
  const s = box.w / w;
  const g = el("g", {}, parent);
  const fo = el("foreignObject", { x: box.x, y: box.y, width: box.w, height: box.h }, g);
  const div = document.createElement("div");
  div.style.cssText = `width:${box.w}px;height:${box.h}px;overflow:hidden;position:relative;background:#fff`;
  const video = document.createElement("video");
  video.src = `../build/${v}/${take.file}`;
  video.muted = true;
  video.preload = "auto";
  video.style.cssText = `position:absolute;left:0;top:0;width:1920px;height:1080px;transform-origin:0 0;transform:scale(${s}) translate(${-cx}px, ${-cy}px)`;
  div.appendChild(video);
  fo.appendChild(div);
  el("rect", { x: box.x, y: box.y, width: box.w, height: box.h, rx: 4, fill: "none", stroke: C.ink, "stroke-width": 1.5 }, g);
  await new Promise((ok) => (video.readyState >= 2 ? ok() : video.addEventListener("loadeddata", ok, { once: true })));
  const seek = (local) => new Promise((ok) => {
    const t = Math.min(Math.max(from + local * speed, 0), take.seconds - 0.05);
    if (Math.abs(video.currentTime - t) < 0.005) return ok();
    video.addEventListener("seeked", ok, { once: true });
    video.currentTime = t;
  });
  return { g, seek };
}
