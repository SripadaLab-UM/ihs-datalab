import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { api } from "@/api/client";
import { Button, Chip, Icon } from "@/components/ui";

import type { Approval } from "./ApprovalCard";
import {
  ADDITIONAL,
  type AnyPlan,
  type PlanSection,
  type PlanV2,
  addableKinds,
  isV2,
  planSections,
  planSummary,
  planTypeLabel,
  requiredKinds,
  withSection,
} from "./plan";

const BOX =
  "mt-1.5 w-full resize-y border border-line bg-transparent px-3 py-2 font-serif text-[16px] leading-relaxed outline-none focus:border-ink";

function rows(text: string): number {
  return Math.max(2, Math.ceil(text.length / 90) + text.split("\n").length - 1);
}

/** An analysis plan to approve (and edit) before the agent touches outcome data. */
export function PlanCard({ conversationId, approval }: { conversationId: string; approval: Approval }) {
  const editable = approval.state === "pending" && isV2(approval.plan);
  // Decided: the plan folds to its question and status; the exact frozen text is a click away.
  const [showPlan, setShowPlan] = useState(false);
  const typeLabel = planTypeLabel(approval.plan);
  const summary = planSummary(approval.plan);
  return (
    <div className={clsx("border-l py-1 pl-5", approval.state === "pending" ? "border-you" : "border-line")}>
      <div className="flex flex-wrap items-baseline gap-2.5">
        <p className="font-serif text-[21px]">Analysis plan</p>
        {typeLabel && <Chip>{typeLabel}</Chip>}
        {approval.state === "pending" && <Chip tone="you">needs you</Chip>}
        {approval.frozen && (
          <Chip tone="good" title={`sha256 ${approval.frozen.sha256}`}>
            <Icon name="lock" size={11} /> frozen {new Date(approval.frozen.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} ·{" "}
            {approval.frozen.sha256.slice(0, 8)}
          </Chip>
        )}
      </div>
      {editable ? (
        <PlanEditor conversationId={conversationId} approval={approval} proposed={approval.plan as PlanV2} />
      ) : (
        <>
          {summary && <p className="mt-1 max-w-[60ch] font-serif text-[16px] leading-relaxed">{summary}</p>}
          <button
            type="button"
            onClick={() => setShowPlan(!showPlan)}
            aria-expanded={showPlan}
            className="mt-2 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
          >
            {showPlan ? "Hide the plan" : approval.frozen ? "Show the frozen plan" : "Show the plan"}
          </button>
          {showPlan && <PlanText plan={approval.plan} />}
          <p className="mt-3 font-sans text-[12.5px] text-muted">
            {approval.frozen
              ? `Approved by you and frozen ${new Date(approval.frozen.at).toLocaleString()}. Later work is labelled as following it or exploratory.`
              : approval.state === "declined"
                ? "Not approved. The agent will ask what to change."
                : approval.state === "withdrawn"
                  ? "Withdrawn (the turn stopped)."
                  : approval.state === "pending"
                    ? "This plan was written by an earlier version of DataLab and can't be edited here."
                    : "Approved."}
          </p>
        </>
      )}
    </div>
  );
}

/** A plan, all of it, exactly as it was approved or proposed. */
function PlanText({ plan }: { plan: AnyPlan | undefined }) {
  const rationale = isV2(plan) ? plan.rationale : "";
  return (
    <dl className="mt-3 grid gap-x-5 gap-y-2.5 font-serif text-[16px] sm:grid-cols-[10rem_1fr]">
      {planTypeLabel(plan) && (
        <div className="contents">
          <dt className="dl-label sm:pt-1.5">Type</dt>
          <dd className="leading-relaxed">
            {planTypeLabel(plan)}
            {rationale && <span className="text-muted">. {rationale}</span>}
          </dd>
        </div>
      )}
      {planSections(plan).map(({ label, content }, i) => (
        <div key={i} className="contents">
          <dt className="dl-label sm:pt-1.5">{label}</dt>
          <dd className="whitespace-pre-wrap leading-relaxed">{content}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The pending plan, editable. The server checks it again in full when it's
 * answered, and says what's wrong; this only saves a round trip for the
 * obvious (a required section left empty).
 */
function PlanEditor({ conversationId, approval, proposed }: { conversationId: string; approval: Approval; proposed: PlanV2 }) {
  const [plan, setPlan] = useState<PlanV2>(proposed);
  const schema = useQuery({ queryKey: ["plan-schema"], queryFn: api.planSchema, staleTime: Infinity }).data;
  const answer = useMutation({
    mutationFn: (approve: boolean) => api.answerApproval(conversationId, approval.id, approve, "", plan),
  });
  // Answered: wait for the chat to catch up rather than offer the buttons again.
  const decided = answer.isPending || answer.isSuccess;
  const required = new Set(requiredKinds(plan, schema));
  const guidance = (kind: string) => schema?.sections.find((s) => s.kind === kind)?.guidance ?? "";
  const unwritten = plan.sections.filter((s) => required.has(s.kind) && !s.content.trim()).map((s) => s.label);
  const addable = addableKinds(plan, schema);
  const canAddOwn = plan.sections.filter((s) => s.kind === ADDITIONAL).length < (schema?.limits.additional ?? 0);

  const update = (index: number, change: Partial<PlanSection>) =>
    setPlan({ ...plan, sections: plan.sections.map((s, i) => (i === index ? { ...s, ...change } : s)) });
  const remove = (index: number) => setPlan({ ...plan, sections: plan.sections.filter((_, i) => i !== index) });
  const add = (kind: string) => {
    if (kind === ADDITIONAL) setPlan({ ...plan, sections: [...plan.sections, { kind, label: "", content: "" }] });
    else if (schema) setPlan(withSection(plan, kind, schema));
  };

  return (
    <>
      <p className="mt-1 max-w-[58ch] font-serif text-[16px] leading-relaxed text-muted italic">
        Before looking at outcome data, the agent writes down what it will do. Edit anything, add or remove optional
        sections, then approve it: it's frozen, and later work is labelled as following it or exploratory. For a
        different kind of analysis, choose Not yet and say which.
      </p>
      <div className="mt-4 flex flex-col gap-4">
        <label className="block">
          <span className="dl-label">Why this kind of analysis</span>
          <textarea
            value={plan.rationale}
            onChange={(e) => setPlan({ ...plan, rationale: e.target.value })}
            rows={rows(plan.rationale)}
            className={BOX}
          />
        </label>
        {plan.sections.map((section, index) => {
          const id = `${approval.id}-section-${index}`;
          const own = section.kind === ADDITIONAL;
          return (
            <div key={index}>
              <div className="flex items-baseline justify-between gap-3">
                {own ? (
                  <input
                    value={section.label}
                    onChange={(e) => update(index, { label: e.target.value })}
                    placeholder="Section title"
                    aria-label="Section title"
                    className="dl-label min-w-0 flex-1 border-b border-line bg-transparent outline-none focus:border-ink"
                  />
                ) : (
                  <label htmlFor={id} className="dl-label" title={guidance(section.kind)}>
                    {section.label}
                  </label>
                )}
                {!required.has(section.kind) && (
                  <button
                    type="button"
                    onClick={() => remove(index)}
                    className="font-sans text-[12.5px] text-muted hover:text-ink"
                    aria-label={`Remove ${section.label || "this section"}`}
                  >
                    Remove
                  </button>
                )}
              </div>
              <textarea
                id={id}
                aria-label={own ? section.label || "Section" : undefined}
                value={section.content}
                placeholder={guidance(section.kind)}
                onChange={(e) => update(index, { content: e.target.value })}
                rows={rows(section.content)}
                className={BOX}
              />
            </div>
          );
        })}
        {(addable.length > 0 || canAddOwn) && (
          <label className="flex items-baseline gap-2 font-sans text-[13px] text-muted">
            <span className="shrink-0">Add a section</span>
            <select
              value=""
              onChange={(e) => e.target.value && add(e.target.value)}
              className="border border-line bg-transparent px-2 py-1 text-ink outline-none focus:border-ink"
            >
              <option value="">Choose…</option>
              {addable.map((kind) => (
                <option key={kind} value={kind}>
                  {schema?.sections.find((s) => s.kind === kind)?.label ?? kind}
                </option>
              ))}
              {canAddOwn && <option value={ADDITIONAL}>A section of your own</option>}
            </select>
          </label>
        )}
      </div>
      {unwritten.length > 0 && (
        <p className="mt-2 font-sans text-[13px] text-muted">
          Still to write: {unwritten.join(", ")}. If one can't be settled yet, say so there and what it depends on.
        </p>
      )}
      {answer.error && <p className="mt-2 text-[13px] text-danger">{answer.error.message}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={() => answer.mutate(false)} disabled={decided}>
          Not yet
        </Button>
        <Button variant="primary" onClick={() => answer.mutate(true)} disabled={decided || unwritten.length > 0}>
          Approve plan
        </Button>
      </div>
    </>
  );
}
