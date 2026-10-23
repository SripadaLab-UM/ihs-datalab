// Where DataLab lives on its site: "" on a person's computer, "/datalab" on
// the lab's server. The backend says so in index.html's <base href> (web.py
// mount_web_ui); without one (Vite's dev server, tests) it's the root.
//
// Only the <base> element counts: with none, document.baseURI is the page's
// own address, which would make "/workspace/c_1" look like a base.

function basePath(): string {
  const href = typeof document === "undefined" ? null : document.querySelector("base[href]")?.getAttribute("href");
  if (!href) return "";
  return new URL(href, window.location.origin).pathname.replace(/\/+$/, "");
}

export const BASE = basePath();

/** A DataLab path ("/api/…", "/session-ended") as the browser must ask for it. */
export function at(path: string): string {
  return path.startsWith("/") ? BASE + path : path;
}
