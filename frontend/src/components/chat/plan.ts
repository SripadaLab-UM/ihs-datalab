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
  /** A revision: the approved plan it replaces, and why. */
  revises?: { plan_id: string; sha256: string };
  revision_reason?: string;
  /** What had returned data in the conversation when it was proposed, recorded by DataLab. */
  proposed_after?: { queries: number; tables: string[]; more_tables: number };
}

/** What a plan is shown against: the approved plan it revises, or the version the person sent back. */
export interface PlanComparison {
  label: string;
  plan: AnyPlan;
  plan_id?: string;
  approved_at?: string;
  sha256?: string;
}

export interface PlanChange {
  label: string;
  status: "added" | "removed" | "changed";
  before?: string;
  after?: string;
}

export interface PlanDiff {
  /** False when the earlier plan is version 1: its parts don't line up with sections. */
  comparable: boolean;
  type?: { before: string; after: string };
  rationale?: { before: string; after: string };
  /** DataLab's record of what had run when each was proposed (why a stale plan is proposed again). */
  record?: { before: string; after: string };
  changes: PlanChange[];
  unchanged: number;
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

// Greek letters that look like a Latin one, and Latin letters that aren't a–z
// but look like one; the backend compares titles the same way (plan_schema.py).
const pairs = (from: string, to: string): Record<string, string> =>
  Object.fromEntries(Array.from(from, (c, i) => [c, to[i]]));
const GREEK = pairs("αβγδεζηικμνοπρστυχωςϲϹϳͿϒϱϐϰΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "abydeznikuvonpotuxwcccjjypbkabezhikmnoptyx");
const LATIN = pairs("ɑıɩɡʊɪʏɴʙʜʟᴀᴄᴅᴇᴊᴋᴍᴏᴘᴛᴜᴠᴡᴢǀƅƄəƏǝɛ", "aiiguiynbhlacdejkmoptuvwzlbbeeee");

/** A section title as it reads, to match it across versions: accents, case,
 * punctuation, and look-alike letters (and I, l, 1; O, 0; u, v) don't count. */
export function titleKey(title: string): string {
  // The table first: decomposing can turn a look-alike into another letter.
  const looked = Array.from(title, (c) => GREEK[c] ?? c).join("");
  const bare = looked.normalize("NFKD").replace(/\p{M}/gu, "");
  const letters = Array.from(bare, (c) => GREEK[c] ?? LATIN[c] ?? (/[\p{L}\p{N}]/u.test(c) ? c.toLowerCase() : " "));
  return letters.join("").replace(/[i1]/g, "l").replace(/0/g, "o").replace(/v/g, "u").split(/\s+/).filter(Boolean).join(" ");
}

/** A comparison from an event, or undefined if there isn't one. */
export function asComparison(value: unknown): PlanComparison | undefined {
  if (!value || typeof value !== "object") return undefined;
  const plan = asPlan((value as PlanComparison).plan);
  return plan ? { ...(value as PlanComparison), plan } : undefined;
}

/**
 * What changed from `before` to `after`, section by section, in `after`'s
 * order and then what was removed. A section is matched by its kind, or by
 * its title for the person's own sections (a renamed one shows as removed
 * and added).
 */
export function planChanges(before: AnyPlan, after: PlanV2): PlanDiff {
  if (!isV2(before)) return { comparable: false, changes: [], unchanged: 0 };
  const key = (s: PlanSection) => (s.kind === ADDITIONAL ? `${ADDITIONAL}:${titleKey(s.label)}` : s.kind);
  const earlier = new Map(before.sections.map((s) => [key(s), s]));
  const seen = new Set<string>();
  const changes: PlanChange[] = [];
  let unchanged = 0;
  for (const section of after.sections) {
    const was = earlier.get(key(section));
    seen.add(key(section));
    if (!was) {
      if (section.content.trim()) changes.push({ label: section.label, status: "added", after: section.content });
    } else if (was.content.trim() !== section.content.trim()) {
      changes.push({ label: section.label, status: "changed", before: was.content, after: section.content });
    } else unchanged++;
  }
  for (const section of before.sections) {
    if (!seen.has(key(section))) changes.push({ label: section.label, status: "removed", before: section.content });
  }
  const diff: PlanDiff = { comparable: true, changes, unchanged };
  if (before.analysis_type !== after.analysis_type) {
    diff.type = { before: before.analysis_type_label, after: after.analysis_type_label };
  }
  if (before.rationale.trim() !== after.rationale.trim()) {
    diff.rationale = { before: before.rationale, after: after.rationale };
  }
  if (JSON.stringify(before.proposed_after ?? null) !== JSON.stringify(after.proposed_after ?? null)) {
    diff.record = { before: proposedAfterText(before), after: proposedAfterText(after) };
  }
  return diff;
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

/** What became of a turn's plan, for the row its work folds into: its last plan counts. */
export function planStatus(
  approvals: {
    approvalKind: string;
    plan?: AnyPlan;
    state: string;
    frozen?: unknown;
    notFrozen?: string;
    changeTypeLabel?: string;
  }[],
): { text: string; tone: "you" | "attn" } | null {
  const plan = approvals.filter((a) => a.approvalKind === "analysis_plan").at(-1);
  if (!plan) return null;
  if (plan.frozen) return { text: isV2(plan.plan) && plan.plan.revises ? "plan revised and frozen" : "plan approved and frozen", tone: "you" };
  if (plan.notFrozen) return { text: "plan approved, not frozen", tone: "attn" };
  if (plan.state === "declined") return { text: plan.changeTypeLabel ? "plan sent back" : "plan not approved", tone: "attn" };
  if (plan.state === "withdrawn") return { text: "plan withdrawn", tone: "attn" };
  return null;
}

// The person's unsaved edits to a pending plan, kept in this tab so a reload
// doesn't lose them. Only a convenience: storage can be unavailable, and the
// server holds the plan itself.
const draftKey = (approvalId: string) => `datalab:plan-draft:${approvalId}`;

/** Edits saved for this pending plan, if they still fit it (same type, same plan revised). */
export function loadDraft(approvalId: string, proposed: PlanV2): PlanV2 | undefined {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(draftKey(approvalId)) ?? "null");
    const draft = asPlan(saved);
    if (!isV2(draft) || draft.analysis_type !== proposed.analysis_type) return undefined;
    if (draft.revises?.plan_id !== proposed.revises?.plan_id) return undefined;
    return draft;
  } catch {
    return undefined;
  }
}

export function saveDraft(approvalId: string, proposed: PlanV2, plan: PlanV2): void {
  try {
    if (JSON.stringify(plan) === JSON.stringify(proposed)) sessionStorage.removeItem(draftKey(approvalId));
    else sessionStorage.setItem(draftKey(approvalId), JSON.stringify(plan));
  } catch {
    // Not kept: the edits live only on the page.
  }
}

/** Every saved draft in this tab, for when the person is signed out. */
export function clearAllDrafts(): void {
  try {
    const keys = Array.from({ length: sessionStorage.length }, (_, i) => sessionStorage.key(i));
    for (const key of keys) if (key?.startsWith("datalab:plan-draft:")) sessionStorage.removeItem(key);
  } catch {
    // Nothing to clear.
  }
}

export function clearDraft(approvalId: string): void {
  try {
    sessionStorage.removeItem(draftKey(approvalId));
  } catch {
    // Nothing to clear.
  }
}

/** What had run when a plan was proposed, in a sentence (as the backend says it, plan_schema.py). */
export function proposedAfterText(plan: AnyPlan | undefined): string {
  const record = isV2(plan) ? plan.proposed_after : undefined;
  if (!record) return "Not recorded (this plan is from an earlier version of DataLab).";
  const notCounted = "Attached files, and files read in the workspace, aren't counted.";
  if (!record.queries) return `No queries had returned data in this conversation. ${notCounted}`;
  const tables = (record.tables.join(", ") || "no tables") + (record.more_tables ? `, and ${record.more_tables} more` : "");
  const one = record.queries === 1;
  return `${record.queries} ${one ? "query" : "queries"} in this conversation had returned data or ${one ? "was" : "were"} still running, reading ${tables}. Results seen before a plan aren't prespecified by it. ${notCounted}`;
}
