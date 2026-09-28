// A lint in test form: something you can click is a button or a link, so it
// shows the hand, takes focus and answers Enter and Space. A div or span with
// onClick does none of that unless it also has a role and a tabIndex (and its
// own key handling). See docs/DESIGN.md, "Cursor, hover and focus".
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { expect, it } from "vitest";

const SRC = join(__dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

// An opening tag, attributes and all: braces are matched three deep, which is
// as deep as the app's JSX attributes go.
const TAG = /<(div|span|li|p|section|article|header|footer|td|tr|img|ul|ol|nav|main|aside)\b((?:[^>{}]|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*?)\/?>/g;

/**
 * The justified exceptions. A scrim (the dimmed layer behind a drawer or a
 * dialog, `inset-0`) closes it on a click outside; the keyboard's way is
 * Escape or the Close button, so the scrim itself is not a control. A dialog's
 * box stops that click from reaching the scrim, and is a dialog, not a button.
 * A row of the SQL results grid (data-row) opens to its full values on a
 * click; the keyboard's way is the grid's one tab stop and the arrow keys
 * (features/sql/Results.tsx), so the rows aren't tab stops of their own.
 */
function allowed(attrs: string): boolean {
  if (/\bdata-row=/.test(attrs) && /onClick=\{\(\) => setCurrent\(r\)\}/.test(attrs)) return true;
  if (/\binset-0\b/.test(attrs) && /onClick=\{\s*(\(\)\s*=>\s*set\w+\((false|"closed")\)|close\w*|onClose)\s*\}/.test(attrs)) return true;
  if (/role="dialog"/.test(attrs) && /onClick=\{\(e\) => e\.stopPropagation\(\)\}/.test(attrs)) return true;
  return false;
}

it("has no clickable div or span without a role and tabIndex", () => {
  const found: string[] = [];
  for (const file of sources(SRC)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(TAG)) {
      const attrs = m[2];
      if (!/\bonClick=/.test(attrs)) continue;
      if (/\brole=/.test(attrs) && /\btabIndex=/.test(attrs)) continue;
      if (allowed(attrs)) continue;
      const line = text.slice(0, m.index).split("\n").length;
      found.push(`${relative(SRC, file)}:${line} <${m[1]} onClick>`);
    }
  }
  expect(found, "use a <button>, or add role, tabIndex and key handling").toEqual([]);
});

it("catches one", () => {
  const sample = `<div className="p-2" onClick={() => go()}>Open</div>`;
  const m = [...sample.matchAll(TAG)][0];
  expect(/\bonClick=/.test(m[2]) && !allowed(m[2])).toBe(true);
});
