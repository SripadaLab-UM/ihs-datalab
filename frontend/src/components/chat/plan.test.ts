import { expect, it } from "vitest";

import type { PlanSchema } from "@/api/client";

import {
  addableKinds,
  planChanges,
  planSections,
  planSummary,
  planTypeLabel,
  requiredKinds,
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
