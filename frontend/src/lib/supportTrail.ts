// The recent trail a support report carries from the browser: failed API
// requests (method, path, status) and errors in the page (the error's class
// only). Never a message, a response, a query string or anything typed. The
// backend keeps only each path's shape (support.py). In memory, for this
// page only: the last 50, from the last 24 hours.

export interface TrailEntry {
  at: number; // milliseconds since the epoch
  kind: "request" | "page_error";
  method?: string;
  path?: string;
  status?: number;
  error?: string;
}

const MAX = 50;
const DAY = 24 * 60 * 60 * 1000;
const entries: TrailEntry[] = [];

export function recordTrail(entry: TrailEntry) {
  entries.push(entry);
  if (entries.length > MAX) entries.splice(0, entries.length - MAX);
}

/** A failed request: its path without the query string or fragment. */
export function recordFailedRequest(method: string | undefined, path: string, status: number) {
  const bare = path.split("?")[0].split("#")[0];
  recordTrail({ at: Date.now(), kind: "request", method: (method ?? "GET").toUpperCase(), path: bare, status });
}

export function recentTrail(now = Date.now()): TrailEntry[] {
  return entries.filter((entry) => now - entry.at <= DAY);
}

export function clearTrail() {
  entries.length = 0;
}

function errorClass(value: unknown): string {
  return value instanceof Error && /^\w{1,60}$/.test(value.name) ? value.name : "Error";
}

if (typeof window !== "undefined") {
  window.addEventListener("error", (event) =>
    recordTrail({ at: Date.now(), kind: "page_error", error: errorClass(event.error) }),
  );
  window.addEventListener("unhandledrejection", (event) =>
    recordTrail({ at: Date.now(), kind: "page_error", error: errorClass(event.reason) }),
  );
}
