// Analysis plans, of either version, as the chat and the exported report show
// them. The sections come from the backend's registry (sessions/plan_schema.py):
// a plan carries its own labels, so a frozen one shows exactly as it was
// approved, and the plan card gets the rest (what's required, what can be
// added) from /api/plan-schema.
import type { PlanSchema } from "@/api/client";

export interface PlanSection {
  kind: string;
  label: string;
  content: string;
}

export interface PlanV2 {
  schema_version: 2;
  analysis_type: string;
  analysis_type_label: string;
  rationale: string;
  sections: PlanSection[];
}

/** Version 1: seven fixed parts, frozen before plans had types. */
export type PlanV1 = Record<string, string>;

export type AnyPlan = PlanV2 | PlanV1;

/** The kind of a section with its own title. */
export const ADDITIONAL = "additional";

// Version 1's parts, with the labels those plans were shown and frozen with.
const V1_LABELS: [string, string][] = [
  ["question", "Question"],
  ["estimand", "Estimand (what exactly is estimated)"],
  ["exposure", "Exposure or predictor"],
  ["outcome", "Outcome"],
  ["covariates", "Covariates and adjustment"],
  ["cohort", "Cohort, time window, and exclusions"],
  ["decisions", "Decisions expected along the way"],
];

export function isV2(plan: AnyPlan | undefined): plan is PlanV2 {
  return !!plan && (plan as PlanV2).schema_version === 2 && Array.isArray((plan as PlanV2).sections);
}

/** A plan from an event, or undefined if there isn't one. */
export function asPlan(value: unknown): AnyPlan | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as AnyPlan) : undefined;
}

/** The plan's sections, in order, as (label, text). */
export function planSections(plan: AnyPlan | undefined): { label: string; content: string }[] {
  if (!plan) return [];
  if (isV2(plan)) return plan.sections.map((s) => ({ label: String(s.label), content: String(s.content) }));
  return V1_LABELS.filter(([name]) => plan[name]).map(([name, label]) => ({ label, content: String(plan[name]) }));
}

/** What the plan is for, in a line: its question. */
export function planSummary(plan: AnyPlan | undefined): string {
  if (!plan) return "";
  if (isV2(plan)) return plan.sections.find((s) => s.kind === "question_and_purpose")?.content ?? "";
  return String(plan.question ?? "");
}

/** The plan's type, as shown ("" for a version-1 plan, which had none). */
export function planTypeLabel(plan: AnyPlan | undefined): string {
  return isV2(plan) ? String(plan.analysis_type_label) : "";
}

/** The sections a plan of its type must have written. */
export function requiredKinds(plan: PlanV2, schema: PlanSchema | undefined): string[] {
  const type = schema?.types.find((t) => t.id === plan.analysis_type);
  return schema && type ? [...schema.core, ...type.required] : [];
}

/** The registered sections this plan could add, in the order they're shown. */
export function addableKinds(plan: PlanV2, schema: PlanSchema | undefined): string[] {
  const type = schema?.types.find((t) => t.id === plan.analysis_type);
  if (!schema || !type) return [];
  const present = new Set(plan.sections.map((s) => s.kind));
  return [...type.optional, ...schema.modules].filter((kind) => !present.has(kind));
}

/** The plan with a registered section added, in the registry's order (additional sections stay last). */
export function withSection(plan: PlanV2, kind: string, schema: PlanSchema): PlanV2 {
  const type = schema.types.find((t) => t.id === plan.analysis_type);
  const order = [...schema.core, ...(type?.required ?? []), ...(type?.optional ?? []), ...schema.modules];
  const label = schema.sections.find((s) => s.kind === kind)?.label ?? kind;
  const rank = (k: string) => (k === ADDITIONAL ? order.length : order.indexOf(k));
  const sections = [...plan.sections, { kind, label, content: "" }];
  // A stable sort keeps additional sections in the order they were added.
  return { ...plan, sections: sections.sort((a, b) => rank(a.kind) - rank(b.kind)) };
}
