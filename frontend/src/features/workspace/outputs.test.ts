import { expect, it } from "vitest";

import { sharedPrefix } from "./SidePanel";

// From the design review: long shared prefixes hid the part that tells outputs apart.
it("finds a long shared start, cut at a separator", () => {
  const names = ["sleep_mood_2025_pilot_flow.csv", "sleep_mood_2025_pilot_model.csv", "sleep_mood_2025_pilot_report.html"];
  expect(sharedPrefix(names)).toBe("sleep_mood_2025_pilot_");
});

it("leaves short or whole-name overlaps alone", () => {
  expect(sharedPrefix(["a_1.csv", "a_2.csv"])).toBe("");
  expect(sharedPrefix(["steps.csv"])).toBe("");
  expect(sharedPrefix(["phq9_table_profile.csv", "phq9_table_profile_dictionary.md"])).toBe("phq9_table_");
  expect(sharedPrefix(["report_final.html", "report_final_v2.html"])).toBe("");
});

it("cuts at a space too, and never takes a whole name", () => {
  expect(sharedPrefix(["Sleep report draft.html", "Sleep report final.html"])).toBe("Sleep report ");
  // "participant_" would leave the first name only "1": too little to show.
  expect(sharedPrefix(["participant_1", "participant_1.csv"])).toBe("");
});
