import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { api } from "@/api/client";
import { Button, Chip, Icon } from "@/components/ui";

import type { Approval } from "./ApprovalCard";
import {
  ADDITIONAL,
  type AnyPlan,
  type PlanComparison,
  type PlanSection,
  type PlanV2,
  addableKinds,
  isV2,
  planChanges,
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

const when = (at: string) => new Date(at).toLocaleString();

/** An analysis plan to approve (and edit) before the agent touches outcome data. */
export function PlanCard({ conversationId, approval }: { conversationId: string; approval: Approval }) {
  const editable = approval.state === "pending" && isV2(approval.plan);
  // Decided: the plan folds to its question and status; the exact frozen text is a click away.
  const [showPlan, setShowPlan] = useState(false);
  const [showChanges, setShowChanges] = useState(false);
  const typeLabel = planTypeLabel(approval.plan);
  const summary = planSummary(approval.plan);
  const revision = isV2(approval.plan) ? approval.plan : undefined;
  return (
    <div className={clsx("border-l py-1 pl-5", approval.state === "pending" ? "border-you" : "border-line")}>
      <div className="flex flex-wrap items-baseline gap-2.5">
        <p className="font-serif text-[21px]">{revision?.revises ? "Revised analysis plan" : "Analysis plan"}</p>
        {typeLabel && <Chip>{typeLabel}</Chip>}
        {approval.state === "pending" && <Chip tone="you">needs you</Chip>}
        {approval.frozen && (
          <Chip tone="good" title={`sha256 ${approval.frozen.sha256}`}>
            <Icon name="lock" size={11} /> frozen {new Date(approval.frozen.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} ·{" "}
            {approval.frozen.sha256.slice(0, 8)}
          </Chip>
        )}
        {approval.supersededBy && <Chip tone="attn">revised later</Chip>}
      </div>
      {revision?.revises && <Revises plan={revision} compareTo={approval.compareTo} />}
      {editable ? (
        <PlanEditor conversationId={conversationId} approval={approval} proposed={approval.plan as PlanV2} />
      ) : (
        <>
          {summary && <p className="mt-1 max-w-[60ch] font-serif text-[16px] leading-relaxed">{summary}</p>}
          <div className="mt-2 flex flex-wrap gap-x-5">
            <Toggle open={showPlan} onClick={() => setShowPlan(!showPlan)}>
              {showPlan ? "Hide the plan" : approval.frozen ? "Show the frozen plan" : "Show the plan"}
            </Toggle>
            {revision && approval.compareTo && (
              <Toggle open={showChanges} onClick={() => setShowChanges(!showChanges)}>
                {showChanges ? "Hide what changed" : "Show what changed"}
              </Toggle>
            )}
          </div>
          {showChanges && revision && approval.compareTo && <PlanChanges before={approval.compareTo} after={revision} />}
          {showPlan && <PlanText plan={approval.plan} />}
          <p className="mt-3 font-sans text-[12.5px] text-muted">
            {approval.supersededBy
              ? `Approved and frozen ${when(approval.frozen?.at ?? approval.supersededBy.at)}. A revision replaced it, frozen ${when(approval.supersededBy.at)}; this version is kept as it was.`
              : approval.frozen
                ? `Approved by you and frozen ${when(approval.frozen.at)}. Later work is labelled as following it or exploratory.`
                : approval.state === "declined"
                  ? approval.changeTypeLabel
                    ? `Sent back: you asked for a ${approval.changeTypeLabel} plan instead.`
                    : "Not approved. The agent will ask what to change."
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

function Toggle({ open, onClick, children }: { open: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      className="font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
    >
      {children}
    </button>
  );
}

/** Which approved plan a revision replaces. */
function Revises({ plan, compareTo }: { plan: PlanV2; compareTo?: PlanComparison }) {
  const sha = plan.revises?.sha256.slice(0, 8);
  return (
    <p className="mt-1 max-w-[60ch] font-sans text-[13px] text-muted">
      Revises the plan {compareTo?.approved_at ? `approved ${when(compareTo.approved_at)}` : "approved earlier"} ({sha}),
      which is kept as it was.
    </p>
  );
}

/** A plan, all of it, exactly as it was approved or proposed. */
function PlanText({ plan }: { plan: AnyPlan | undefined }) {
  const rationale = isV2(plan) ? plan.rationale : "";
  const reason = isV2(plan) ? plan.revision_reason : "";
  return (
    <dl className="mt-3 grid gap-x-5 gap-y-2.5 font-serif text-[16px] sm:grid-cols-[10rem_1fr]">
      {reason && (
        <div className="contents">
          <dt className="dl-label sm:pt-1.5">Why it changed</dt>
          <dd className="whitespace-pre-wrap leading-relaxed">{reason}</dd>
        </div>
      )}
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

const STATUS = {
  added: { text: "added", tone: "good" },
  removed: { text: "removed", tone: "bad" },
  changed: { text: "changed", tone: "attn" },
} as const;

/** What changed from the plan this one is compared with, section by section. */
function PlanChanges({ before, after }: { before: PlanComparison; after: PlanV2 }) {
  const diff = planChanges(before.plan, after);
  const heading = `Changes from ${before.label.charAt(0).toLowerCase()}${before.label.slice(1)}`;
  if (!diff.comparable) {
    return (
      <details className="mt-3 border border-line px-3 py-2.5">
        <summary className="dl-label cursor-pointer">{heading}: it used the earlier plan format, shown in full</summary>
        <PlanText plan={before.plan} />
      </details>
    );
  }
  const none = !diff.type && !diff.rationale && diff.changes.length === 0;
  return (
    <div className="mt-3 border border-line px-3 py-2.5">
      <p className="dl-label">{heading}</p>
      {none && <p className="mt-1.5 font-sans text-[13px] text-muted">No changes.</p>}
      <ul className="mt-1.5 flex flex-col gap-2.5 font-serif text-[15.5px] leading-relaxed">
        {diff.type && (
          <li>
            <Chip tone="attn">type changed</Chip> {diff.type.before} → {diff.type.after}
          </li>
        )}
        {diff.rationale && <Change label="Why this kind of analysis" status="changed" before={diff.rationale.before} after={diff.rationale.after} />}
        {diff.changes.map((change, i) => (
          <Change key={i} {...change} />
        ))}
      </ul>
      {diff.unchanged > 0 && (
        <p className="mt-2 font-sans text-[12.5px] text-muted">
          {diff.unchanged} other section{diff.unchanged > 1 ? "s" : ""} unchanged.
        </p>
      )}
    </div>
  );
}

function Change({ label, status, before, after }: { label: string; status: keyof typeof STATUS; before?: string; after?: string }) {
  return (
    <li>
      <p className="flex flex-wrap items-baseline gap-2">
        <span className="dl-label">{label}</span> <Chip tone={STATUS[status].tone}>{STATUS[status].text}</Chip>
      </p>
      {before !== undefined && (
        <p className={clsx("mt-0.5 whitespace-pre-wrap text-muted", status === "removed" && "line-through")}>
          {status === "changed" && <span className="font-sans text-[12px] not-italic">Was: </span>}
          {before}
        </p>
      )}
      {after !== undefined && (
        <p className="mt-0.5 whitespace-pre-wrap">
          {status === "changed" && <span className="font-sans text-[12px] text-muted">Now: </span>}
          {after}
        </p>
      )}
    </li>
  );
}

/**
 * The pending plan, editable. The server checks it again in full when it's
 * answered, and says what's wrong; this only saves a round trip for the
 * obvious (a required section left empty).
 */
function PlanEditor({ conversationId, approval, proposed }: { conversationId: string; approval: Approval; proposed: PlanV2 }) {
  const [plan, setPlan] = useState<PlanV2>(proposed);
  const [otherType, setOtherType] = useState<string | null>(null);
  const schema = useQuery({ queryKey: ["plan-schema"], queryFn: api.planSchema, staleTime: Infinity }).data;
  const answer = useMutation({
    mutationFn: ({ approve, changeType }: { approve: boolean; changeType?: string }) =>
      api.answerApproval(conversationId, approval.id, approve, "", plan, changeType),
  });
  // Answered: wait for the chat to catch up rather than offer the buttons again.
  const decided = answer.isPending || answer.isSuccess;
  const required = new Set(requiredKinds(plan, schema));
  const guidance = (kind: string) => schema?.sections.find((s) => s.kind === kind)?.guidance ?? "";
  const unwritten = plan.sections.filter((s) => required.has(s.kind) && !s.content.trim()).map((s) => s.label);
  if (plan.revises && !plan.revision_reason?.trim()) unwritten.unshift("What changes, and why");
  const addable = addableKinds(plan, schema);
  const canAddOwn = plan.sections.filter((s) => s.kind === ADDITIONAL).length < (schema?.limits.additional ?? 0);
  const otherTypes = schema?.types.filter((t) => t.id !== plan.analysis_type) ?? [];
  const chosen = otherTypes.find((t) => t.id === otherType);

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
        {plan.revises
          ? "The agent wants to change the approved plan. Check what changed, edit anything, then approve the revision: it's frozen as a new version, and later work follows it."
          : "Before looking at outcome data, the agent writes down what it will do. Edit anything, add or remove optional sections, then approve it: it's frozen, and later work is labelled as following it or exploratory."}
      </p>
      {approval.compareTo && <PlanChanges before={approval.compareTo} after={plan} />}
      <div className="mt-4 flex flex-col gap-4">
        {plan.revises && (
          <label className="block">
            <span className="dl-label">What changes, and why</span>
            <textarea
              value={plan.revision_reason ?? ""}
              onChange={(e) => setPlan({ ...plan, revision_reason: e.target.value })}
              rows={rows(plan.revision_reason ?? "")}
              className={BOX}
            />
          </label>
        )}
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
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
        {otherTypes.length > 0 && otherType === null && (
          <button
            type="button"
            onClick={() => setOtherType("")}
            disabled={decided}
            className="mr-auto font-sans text-[13px] text-muted underline decoration-faint underline-offset-4 hover:text-ink"
          >
            A different kind of analysis?
          </button>
        )}
        <Button onClick={() => answer.mutate({ approve: false })} disabled={decided}>
          Not yet
        </Button>
        <Button variant="primary" onClick={() => answer.mutate({ approve: true })} disabled={decided || unwritten.length > 0}>
          {plan.revises ? "Approve revision" : "Approve plan"}
        </Button>
      </div>
      {otherType !== null && (
        <div className="mt-3 border border-line px-3 py-2.5 font-sans text-[13px]">
          <label className="flex flex-wrap items-baseline gap-2 text-muted">
            <span className="shrink-0">Send it back as a</span>
            <select
              value={otherType}
              onChange={(e) => setOtherType(e.target.value)}
              className="border border-line bg-transparent px-2 py-1 text-ink outline-none focus:border-ink"
            >
              <option value="">Choose a type…</option>
              {otherTypes.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
            <span className="shrink-0">plan</span>
          </label>
          {chosen && <p className="mt-1.5 text-muted">{chosen.summary}</p>}
          <p className="mt-1.5 text-muted">
            The agent rewrites it as that type, keeping your edits, and you'll see what changed before approving.
          </p>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOtherType(null)} disabled={decided}>
              Cancel
            </Button>
            <Button onClick={() => answer.mutate({ approve: false, changeType: otherType })} disabled={decided || !chosen}>
              Send back
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
