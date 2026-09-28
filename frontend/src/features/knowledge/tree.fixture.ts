// A made-up knowledge base in the real layout, for the tree's tests.
import type { KbEntry } from "@/api/knowledge";

export const entry = (path: string, place: string, title = path.split("/").at(-1)!, more: Partial<KbEntry> = {}): KbEntry => ({
  path, place, title, summary: "", size: 10, status: null, kind: null, related: [], cohorts: [], ...more,
}); // prettier-ignore

export const FIXTURE: KbEntry[] = [
  entry("AGENTS.md", "top"),
  entry("README.md", "top"),
  entry("index.md", "top"),
  entry("sources/wristband.md", "page", "wristband", {
    summary: "Wristband trackers.",
    related: ["tables/IHS_2031.BANDDAILY"],
  }),
  entry("sources/ring.md", "page", "ring", { summary: "Smart rings." }),
  entry("sources/diary.md", "page", "diary"),
  // Linked from the table's side, and one in an older cohort's schema.
  entry("tables/IHS_2031.BANDDAILY.md", "page", "IHS_2031.BANDDAILY"),
  entry("tables/IHS_2030.BANDDAILY.md", "page", "IHS_2030.BANDDAILY", {
    related: ["sources/wristband"],
  }),
  entry("tables/IHS_2031.RINGSLEEP.md", "page", "IHS_2031.RINGSLEEP", {
    related: ["sources/ring.md"],
    cohorts: ["2031"],
    summary: "IHS_2031.RINGSLEEP: Ring sleep table.",
  }),
  entry("tables/IHS_2031.LOOKUP.md", "page", "IHS_2031.LOOKUP"),
  entry("features/nap_minutes.md", "page", "nap_minutes", {
    status: "draft",
    summary: "Minutes napped a day.",
  }),
  entry("qc/nap_window.md", "page", "nap_window", {
    cohorts: ["2030", "2031"],
  }),
  entry("papers/naps-and-mood.md", "page", "naps-and-mood"),
  entry("generated/drift.md", "generated", "drift.md"),
  entry("skills/nap-check/SKILL.md", "skill", "nap-check"),
  entry("skills/nap-check/check.R", "skill_file", "check.R"),
];
