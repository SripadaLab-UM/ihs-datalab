import { describe, expect, it } from "vitest";

import type { KbEntry, KnowledgeStatus } from "@/api/knowledge";

import { findPage, groupPages, resolveLink, statusTone } from "./pages";
import { ago, devicePage, repoState } from "./repoState";

const entry = (path: string, place: string, title = path.split("/").at(-1)!, summary = ""): KbEntry => ({
  path, place, title, summary, size: 10, status: null, kind: null,
}); // prettier-ignore

const ENTRIES = [
  entry("AGENTS.md", "top"),
  entry("index.md", "top"),
  entry("sources/oura.md", "page", "oura", "Oura rings."),
  entry("sources/fitbit.md", "page", "fitbit", "Fitbit trackers."),
  entry("qc/midnight-sleep.md", "page", "midnight-sleep"),
  entry("skills/steps-check/check.R", "skill_file"),
  entry("skills/steps-check/SKILL.md", "skill", "steps-check"),
  entry("generated/drift.md", "generated"),
];

describe("groupPages", () => {
  it("groups by the layout's folders, in order, leaving out empty ones", () => {
    const groups = groupPages(ENTRIES);
    expect(groups.map((g) => g.title)).toEqual(["About", "Data sources", "QC rules", "Lab skills", "Generated"]);
    expect(groups[1].entries.map((e) => e.title)).toEqual(["fitbit", "oura"]);
    // A skill's SKILL.md comes first, then its files.
    expect(groups[3].entries.map((e) => e.path)).toEqual(["skills/steps-check/SKILL.md", "skills/steps-check/check.R"]);
  });

  it("filters on path, title and summary, every word", () => {
    expect(groupPages(ENTRIES, "trackers").flatMap((g) => g.entries.map((e) => e.path))).toEqual(["sources/fitbit.md"]);
    expect(groupPages(ENTRIES, "sources fit").flatMap((g) => g.entries.map((e) => e.path))).toEqual(["sources/fitbit.md"]);
    expect(groupPages(ENTRIES, "nothing like it")).toEqual([]);
  });
});

describe("resolveLink", () => {
  it.each([
    ["sources/fitbit.md", "../qc/midnight-sleep.md", "qc/midnight-sleep.md"],
    ["sources/fitbit.md", "oura.md", "sources/oura.md"],
    ["index.md", "sources/fitbit.md#steps", "sources/fitbit.md"],
    ["sources/fitbit.md", "/qc/midnight-sleep.md", "qc/midnight-sleep.md"],
    ["sources/fitbit.md", "../../etc/passwd", null],
    ["sources/fitbit.md", "https://example.org/x.md", null],
    ["sources/fitbit.md", "mailto:a@b.c", null],
    ["sources/fitbit.md", "//evil.example/x", null],
    ["sources/fitbit.md", "#section", null],
  ])("%s → %s", (from, href, expected) => {
    expect(resolveLink(from, href)).toBe(expected);
  });

  it("finds a related page by its path without .md", () => {
    expect(findPage(ENTRIES, "qc/midnight-sleep")?.path).toBe("qc/midnight-sleep.md");
    expect(findPage(ENTRIES, "qc/nothing")).toBeUndefined();
  });

  it("colours statuses by what they mean", () => {
    expect([statusTone("reviewed"), statusTone("draft"), statusTone("deprecated"), statusTone(null)]).toEqual([
      "good", "attn", "bad", undefined,
    ]); // prettier-ignore
  });
});

describe("repoState", () => {
  const status = (repo: KnowledgeStatus["repo"], more: Partial<KnowledgeStatus> = {}): KnowledgeStatus => ({
    available: true, repo, name: "SripadaLab-UM/ihs-knowledge", signed_in: true, account: null, head: null,
    last_sync: null, last_error: null, ahead: 0, behind: 0, message: null, ...more,
  }); // prettier-ignore

  it("says each state in plain words", () => {
    expect(repoState(status("in sync"))).toMatchObject({ label: "up to date", tone: "good", canSync: true });
    expect(repoState(status("behind", { behind: 3 })).text).toMatch(/GitHub has 3 newer commits/);
    expect(repoState(status("diverged", { ahead: 1, behind: 2 }))).toMatchObject({ label: "diverged", tone: "bad" });
    expect(repoState(status("not cloned")).text).toMatch(/Press Sync/);
    expect(repoState(status("no access", { message: "Ask Ali to add you." }))).toMatchObject({ label: "no access", text: "Ask Ali to add you." });
    expect(repoState(status("sync failed", { message: "git fetch took too long." })).text).toBe("git fetch took too long.");
    expect(repoState(status("signed out", { message: "The GitHub sign-in has run out. Sign in again." }))).toMatchObject({
      label: "signed out",
      canSync: false,
      text: "The GitHub sign-in has run out. Sign in again.",
    });
    expect(repoState(status("not configured", { available: false })).canSync).toBe(false);
  });

  it("says how long ago", () => {
    const now = Date.parse("2026-09-27T12:00:00Z");
    expect(ago("2026-09-27T11:59:30Z", now)).toBe("just now");
    expect(ago("2026-09-27T11:55:00Z", now)).toBe("5 minutes ago");
    expect(ago("2026-09-27T09:00:00Z", now)).toBe("3 hours ago");
    expect(ago("2026-09-25T12:00:00Z", now)).toBe("2 days ago");
    expect(ago("not a date", now)).toBe("not a date");
  });

  it("links only to GitHub's own device page", () => {
    expect(devicePage("https://github.com/login/device")).toBe("https://github.com/login/device");
    expect(devicePage("https://evil.example/login/device")).toBe("https://github.com/login/device");
    expect(devicePage(null)).toBe("https://github.com/login/device");
  });
});
