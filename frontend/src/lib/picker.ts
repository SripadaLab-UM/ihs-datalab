// What to say while the computer's own file or folder picker is open. The
// picker is a window of its own, outside the browser, and on some computers
// it can open behind the browser, which looks as if nothing happened.

/** "A folder picker is open…", for a status line while DataLab waits for the choice. */
export function pickerOpenHint(what: "folder" | "files"): string {
  const picker = what === "folder" ? "A folder picker" : "A file picker";
  return `${picker} is open: choose there. If you can't see it, it may be behind this window. Look for it in the taskbar or the Dock.`;
}
