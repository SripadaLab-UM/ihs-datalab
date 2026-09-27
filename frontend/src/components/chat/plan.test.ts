import { expect, it } from "vitest";

import type { PlanSchema } from "@/api/client";

import {
  addableKinds,
  planChanges,
  planSections,
  planStatus,
  proposedAfterText,
  clearAllDrafts,
  planSummary,
  planTypeLabel,
  requiredKinds,
  titleKey,
  withSection,
  type PlanV2,
} from "./plan";

const schema = {
  core: ["question_and_purpose", "deliverables"],
  modules: ["repeated_observations", "missing_data"],
  types: [{ id: "association", label: "Association or estimation", summary: "", required: ["method"], optional: [], checks: "" }],
  sections: [{ kind: "repeated_observations", label: "Repeated observations", guidance: "" }],
  limits: { section: 2000, title: 80, rationale: 300, reason: 500, additional: 3, plan: 12000 },
  schema_version: 2,
} as PlanSchema;

const plan: PlanV2 = {
  schema_version: 2,
  analysis_type: "association",
  analysis_type_label: "Association or estimation",
  rationale: "",
  sections: [
    { kind: "question_and_purpose", label: "Question and purpose", content: "Sleep and mood?" },
    { kind: "deliverables", label: "Deliverables", content: "A report." },
    { kind: "method", label: "Method and adjustment", content: "Mixed model." },
    { kind: "missing_data", label: "Missing data", content: "Complete cases." },
    { kind: "additional", label: "Pilot", content: "50 interns." },
  ],
};

it("reads plans of either version", () => {
  expect(planSections(plan).map((s) => s.label)).toEqual([
    "Question and purpose",
    "Deliverables",
    "Method and adjustment",
    "Missing data",
    "Pilot",
  ]);
  expect(planSummary(plan)).toBe("Sleep and mood?");
  expect(planTypeLabel(plan)).toBe("Association or estimation");
  const v1 = { question: "Sleep?", decisions: "Drop naps.", outcome: "" };
  expect(planSections(v1)).toEqual([
    { label: "Question", content: "Sleep?" },
    { label: "Decisions expected along the way", content: "Drop naps." },
  ]);
  expect(planSummary(v1)).toBe("Sleep?");
  expect(planTypeLabel(v1)).toBe("");
});

it("knows what a plan must have, and adds sections in the registry's order", () => {
  expect(requiredKinds(plan, schema)).toEqual(["question_and_purpose", "deliverables", "method"]);
  expect(addableKinds(plan, schema)).toEqual(["repeated_observations"]);
  expect(withSection(plan, "repeated_observations", schema).sections.map((s) => s.kind)).toEqual([
    "question_and_purpose",
    "deliverables",
    "method",
    "repeated_observations",
    "missing_data",
    "additional",
  ]);
});

it("says what changed between two versions, section by section", () => {
  const after: PlanV2 = {
    ...plan,
    analysis_type: "prediction",
    analysis_type_label: "Prediction",
    sections: [
      { ...plan.sections[0], content: "Can sleep predict mood?" },
      plan.sections[1],
      { kind: "validation", label: "Validation and performance", content: "Train 2024, test 2025." },
      { kind: "additional", label: "pilot ", content: "50 interns." }, // same title, spaced differently
    ],
  };
  expect(planChanges(plan, after)).toEqual({
    comparable: true,
    type: { before: "Association or estimation", after: "Prediction" },
    changes: [
      { label: "Question and purpose", status: "changed", before: "Sleep and mood?", after: "Can sleep predict mood?" },
      { label: "Validation and performance", status: "added", after: "Train 2024, test 2025." },
      { label: "Method and adjustment", status: "removed", before: "Mixed model." },
      { label: "Missing data", status: "removed", before: "Complete cases." },
    ],
    unchanged: 2,
  });
  expect(planChanges({ question: "v1" }, plan).comparable).toBe(false);
});

it("matches a person's own section across versions however its title is written", () => {
  const before: PlanV2 = { ...plan, sections: [{ kind: "additional", label: "Pilot cohort", content: "50." }] };
  const after: PlanV2 = { ...plan, sections: [{ kind: "additional", label: "Pil\u03bft c\u00f3hort!", content: "100." }] };
  expect(planChanges(before, after).changes).toEqual([
    { label: "Pil\u03bft c\u00f3hort!", status: "changed", before: "50.", after: "100." },
  ]);
  expect(titleKey("Deliver\u0251bles:")).toBe(titleKey("deliverables"));
  // As the backend compares them (plan_schema.py): capitals too, and I/l/1, O/0.
  expect(titleKey("\u0397\u03a5")).toBe("hy");
  expect(titleKey("\u03f9HECKS")).toBe(titleKey("checks"));
  expect(titleKey("DeIiverab1es")).toBe(titleKey("Deliverables"));
  expect(titleKey("Questi0n")).toBe(titleKey("question"));
  expect(titleKey("R\u03b5\u03c5\u03b9s\u03b5s")).toBe(titleKey("Revises"));
  expect(titleKey("Typ\u0259 Deliverab\u01c0es")).toBe(titleKey("Type deliverables"));
});

it("names what became of a turn's last plan", () => {
  const declined = { approvalKind: "analysis_plan", state: "declined" };
  const frozen = { approvalKind: "analysis_plan", state: "approved", frozen: { at: "", sha256: "" }, plan };
  const helper = { approvalKind: "research_helper", state: "declined" };
  expect(planStatus([declined, frozen, helper])).toEqual({ text: "plan approved and frozen", tone: "you" });
  expect(planStatus([frozen, declined])).toEqual({ text: "plan not approved", tone: "attn" });
  const revision = { ...frozen, plan: { ...plan, revises: { plan_id: "pl_1", sha256: "a" }, revision_reason: "x" } };
  expect(planStatus([revision])?.text).toBe("plan revised and frozen");
  expect(planStatus([{ ...declined, changeTypeLabel: "Prediction" }])?.text).toBe("plan sent back");
  expect(planStatus([{ approvalKind: "analysis_plan", state: "approved", notFrozen: "x" }])?.text).toBe("plan approved, not frozen");
  expect(planStatus([helper])).toBeNull();
});

it("says what the record of what ran doesn't count, and clears every draft on sign-out", () => {
  expect(proposedAfterText({ ...plan, proposed_after: { queries: 0, tables: [], more_tables: 0 } })).toContain("aren't counted");
  sessionStorage.setItem("datalab:plan-draft:ap1", "{}");
  sessionStorage.setItem("datalab:other", "kept");
  clearAllDrafts();
  expect(sessionStorage.getItem("datalab:plan-draft:ap1")).toBeNull();
  expect(sessionStorage.getItem("datalab:other")).toBe("kept");
});

it("shows when the record of what had run changed, and says a single query was running", () => {
  const stale: PlanV2 = { ...plan, proposed_after: { queries: 0, tables: [], more_tables: 0 } };
  const again: PlanV2 = { ...plan, proposed_after: { queries: 1, tables: ["IHS_2025.VW_DAILY_MOOD"], more_tables: 0 } };
  const diff = planChanges(stale, again);
  expect(diff.record?.before).toMatch(/^No queries had returned data/);
  expect(diff.record?.after).toMatch(/^1 query in this conversation had returned data or was still running, reading IHS_2025.VW_DAILY_MOOD/);
  expect(diff.changes).toEqual([]);
  expect(planChanges(again, again).record).toBeUndefined();
});
