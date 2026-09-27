import { useMutation } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import clsx from "clsx";

import { Button, Chip, Icon } from "@/components/ui";

import { Markdown } from "./Markdown";

export interface Approval {
  kind: "approval";
  id: string;
  approvalKind: "research_helper" | "analysis_plan";
  question: string;
  plan?: Record<string, string>;
  frozen?: { at: string; sha256: string };
  state: "pending" | "approved" | "declined" | "withdrawn";
  sent?: string;
  answer?: string;
  answerStatus?: string;
}

// The plan's parts, as the backend names them (sessions/plans.py FIELDS).
const PLAN_FIELDS: [string, string][] = [
  ["question", "Question"],
  ["estimand", "Estimand (what exactly is estimated)"],
  ["exposure", "Exposure or predictor"],
  ["outcome", "Outcome"],
  ["covariates", "Covariates and adjustment"],
  ["cohort", "Cohort, time window, and exclusions"],
  ["decisions", "Decisions expected along the way"],
];

/** An analysis plan to approve (and edit) before the agent touches outcome data. */
function PlanCard({ conversationId, approval }: { conversationId: string; approval: Approval }) {
  const [plan, setPlan] = useState<Record<string, string>>(approval.plan ?? {});
  const answer = useMutation({
    mutationFn: (approve: boolean) => api.answerApproval(conversationId, approval.id, approve, "", plan),
  });
  const decided = answer.isPending || answer.isSuccess;
  const shown = approval.frozen ? (approval.plan ?? plan) : plan;
  // Decided: the plan folds to its question and status; the exact frozen text is a click away.
  const [showPlan, setShowPlan] = useState(false);
  return (
    <div className={clsx("border-l py-1 pl-5", approval.state === "pending" ? "border-you" : "border-line")}>
      <div className="flex flex-wrap items-baseline gap-2.5">
        <p className="font-serif text-[21px]">Analysis plan</p>
        {approval.state === "pending" && <Chip tone="you">needs you</Chip>}
        {approval.frozen && (
          <Chip tone="good" title={`sha256 ${approval.frozen.sha256}`}>
            <Icon name="lock" size={11} /> frozen {new Date(approval.frozen.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} ·{" "}
            {approval.frozen.sha256.slice(0, 8)}
          </Chip>
        )}
      </div>
      {approval.state === "pending" ? (
        <>
          <p className="mt-1 max-w-[58ch] font-serif text-[16px] leading-relaxed text-muted italic">
            Before looking at outcome data, the agent writes down what it will do. Edit anything, then approve it: it's
            frozen, and later work is labelled as following it or exploratory.
          </p>
          <div className="mt-4 flex flex-col gap-4">
            {PLAN_FIELDS.map(([name, label]) => (
              <label key={name} className="block">
                <span className="dl-label">{label}</span>
                <textarea
                  value={plan[name] ?? ""}
                  onChange={(e) => setPlan({ ...plan, [name]: e.target.value })}
                  rows={Math.max(2, Math.ceil((plan[name] ?? "").length / 90) + (plan[name] ?? "").split("\n").length - 1)}
                  className="mt-1.5 w-full resize-y border border-line bg-transparent px-3 py-2 font-serif text-[16px] leading-relaxed outline-none focus:border-ink"
                />
              </label>
            ))}
          </div>
          {answer.error && <p className="mt-2 text-[13px] text-danger">{answer.error.message}</p>}
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => answer.mutate(false)} disabled={decided}>
              Not yet
            </Button>
            <Button variant="primary" onClick={() => answer.mutate(true)} disabled={decided || !plan.question?.trim()}>
              Approve plan
            </Button>
          </div>
        </>
      ) : (
        <>
          {shown.question && <p className="mt-1 max-w-[60ch] font-serif text-[16px] leading-relaxed">{shown.question}</p>}
          <button
            type="button"
            onClick={() => setShowPlan(!showPlan)}
            aria-expanded={showPlan}
            className="mt-2 font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
          >
            {showPlan ? "Hide the plan" : approval.frozen ? "Show the frozen plan" : "Show the plan"}
          </button>
          {showPlan && (
            <dl className="mt-3 grid gap-x-5 gap-y-2.5 font-serif text-[16px] sm:grid-cols-[10rem_1fr]">
              {PLAN_FIELDS.filter(([name]) => shown[name]).map(([name, label]) => (
                <div key={name} className="contents">
                  <dt className="dl-label sm:pt-1.5">{label}</dt>
                  <dd className="whitespace-pre-wrap leading-relaxed">{shown[name]}</dd>
                </div>
              ))}
            </dl>
          )}
          <p className="mt-3 font-sans text-[12.5px] text-muted">
            {approval.frozen
              ? `Approved by you and frozen ${new Date(approval.frozen.at).toLocaleString()}. Later work is labelled as following it or exploratory.`
              : approval.state === "declined"
                ? "Not approved. The agent will ask what to change."
                : approval.state === "withdrawn"
                  ? "Withdrawn (the turn stopped)."
                  : "Approved."}
          </p>
        </>
      )}
    </div>
  );
}

/**
 * The research helper's approval card. A data-session agent asked to look
 * something up; nothing leaves until the person approves the exact text,
 * which they may edit first.
 */
export function ApprovalCard({ conversationId, approval }: { conversationId: string; approval: Approval }) {
  if (approval.approvalKind === "analysis_plan") return <PlanCard conversationId={conversationId} approval={approval} />;
  return <HelperCard conversationId={conversationId} approval={approval} />;
}

function HelperCard({ conversationId, approval }: { conversationId: string; approval: Approval }) {
  const [question, setQuestion] = useState(approval.question);
  // Counted as the server counts: by character, not by UTF-16 unit.
  const length = Array.from(question).length;
  const unusual = Array.from(question).filter((c) => !/[\x20-\x7e\n]/.test(c)).length;
  // The box grows with its text, so no line can hide out of sight.
  const box = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight + 2}px`;
  }, [question, approval.state]);
  const answer = useMutation({
    mutationFn: (approve: boolean) => api.answerApproval(conversationId, approval.id, approve, question),
  });
  // Answered: wait for the chat to catch up rather than offer the buttons again.
  const decided = answer.isPending || answer.isSuccess;

  return (
    <div className={clsx("border-l py-1 pl-5 text-[14px]", approval.state === "pending" ? "border-research" : "border-line")}>
      <div className="flex flex-wrap items-baseline gap-2.5">
        <Icon name="globe" size={15} className="translate-y-[2px] text-research" />
        <p className="font-serif text-[21px]">The agent wants to look something up online</p>
        {approval.state === "pending" && <Chip tone="you">needs you</Chip>}
      </div>
      {approval.state === "pending" ? (
        <>
          <p className="mt-1 max-w-[58ch] font-serif text-[16px] leading-relaxed text-muted italic">
            The helper has the internet and sees only this question: not the conversation, files, or data. Check it
            contains no study data before sending. You can edit it.
          </p>
          <textarea
            ref={box}
            value={question}
            dir="ltr"
            onChange={(e) => setQuestion(e.target.value)}
            className="mt-3 w-full resize-none overflow-hidden border border-line bg-transparent p-2.5 font-mono text-xs outline-none focus:border-ink"
          />
          <p className={`text-right text-xs ${length > 1000 ? "text-danger" : "text-muted"}`}>
            {length} / 1000 characters
          </p>
          {/* Exactly what will be sent, all of it, with anything that isn't plain
              English letters highlighted (look-alike letters could carry data). */}
          <p className="dl-label mt-3">What will be sent</p>
          <p dir="ltr" className="mt-1.5 whitespace-pre-wrap break-words bg-sunken p-2.5 font-mono text-xs">
            {Array.from(question).map((char, i) =>
              /[\x20-\x7e\n]/.test(char) ? (
                char
              ) : (
                <mark key={i} className="rounded bg-research-soft text-research" title={`U+${char.codePointAt(0)!.toString(16).toUpperCase()}`}>
                  {char}
                </mark>
              ),
            )}
          </p>
          {unusual > 0 && (
            <p className="mt-1 text-xs text-research">
              {unusual} character{unusual > 1 ? "s aren't" : " isn't"} plain English letters or symbols (highlighted). Check
              {unusual > 1 ? " they're" : " it's"} meant to be there.
            </p>
          )}
          {answer.error && <p className="mt-1 text-xs text-danger">{answer.error.message}</p>}
          <div className="mt-2 flex justify-end gap-2">
            <Button onClick={() => answer.mutate(false)} disabled={decided}>
              Don't send
            </Button>
            <Button
              variant="primary"
              onClick={() => answer.mutate(true)}
              disabled={decided || !question.trim() || length > 1000}
            >
              Send to the research helper
            </Button>
          </div>
        </>
      ) : (
        <>
          <pre className="mt-2 whitespace-pre-wrap bg-sunken p-2.5 font-mono text-xs">
            {approval.sent ?? approval.question}
          </pre>
          <p className="mt-1 text-xs text-muted">
            {approval.state === "approved" && !approval.answer && "Sent. The research helper is working…"}
            {approval.state === "declined" && "You didn't send this. The agent was told."}
            {approval.state === "withdrawn" && "This request was withdrawn (the turn stopped)."}
          </p>
          {approval.answer && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-muted">
                {approval.answerStatus === "answered" ? "The helper's answer" : "No answer came back"}
              </summary>
              <div className="mt-1">
                <Markdown text={approval.answer} />
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
