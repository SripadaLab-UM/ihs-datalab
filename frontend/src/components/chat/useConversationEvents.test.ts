import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, it } from "vitest";

import { EVENT_TYPES } from "./useConversationEvents";

const source = (file: string) => readFileSync(join(__dirname, file), "utf8");
const names = (text: string, pattern: RegExp) => [...text.matchAll(pattern)].map((m) => m[1]);

// The stream delivers only the event types listed: one the chat handles but
// isn't listed silently never arrives (title_changed did exactly that).
it("listens for every event type the chat handles", () => {
  const transcript = source("transcript.ts");
  // The cases of the transcript's switch over event types.
  const start = transcript.indexOf("switch (event.type) {");
  const body = transcript.slice(start, transcript.indexOf("\n    }\n", start));
  // The chat, and its hooks that refresh on events (moved from Chat.tsx).
  const chat = ["Chat.tsx", "chatHooks.ts"].map(source).join("\n");
  const handled = new Set([
    ...names(body, /case "([a-z_]+)"/g),
    ...names(transcript, /event\.type === "([a-z_]+)"/g),
    ...names(chat, /e\.type === "([a-z_]+)"/g),
    ...[...chat.matchAll(/\[([^\]]*)\]\.includes\(e\.type\)/g)].flatMap((m) => names(m[1], /"([a-z_]+)"/g)),
  ]);
  expect(handled.size).toBeGreaterThan(10); // the patterns still find the handlers
  expect([...handled].filter((type) => !EVENT_TYPES.includes(type))).toEqual([]);
});
