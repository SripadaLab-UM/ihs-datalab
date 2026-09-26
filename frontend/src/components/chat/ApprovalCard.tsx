import { useMutation } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { Button } from "@/components/ui";

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
  return (
    <div className="rounded-xl border border-accent/40 bg-accent-soft/40 p-4 text-sm">
      <p className="font-medium">📋 Analysis plan{approval.frozen ? " (approved and frozen)" : ""}</p>
      {approval.state === "pending" ? (
        <>
          <p className="mt-1 text-xs text-muted">
            The agent proposes this plan before looking at outcome data. Edit anything, then approve it. Once approved
            it's frozen, and later work is labelled as following it or exploratory.
          </p>
          <div className="mt-2 flex flex-col gap-2">
            {PLAN_FIELDS.map(([name, label]) => (
              <label key={name} className="block text-xs">
                <span className="font-medium">{label}</span>
                <textarea
                  value={plan[name] ?? ""}
                  onChange={(e) => setPlan({ ...plan, [name]: e.target.value })}
                  rows={Math.max(2, (plan[name] ?? "").split("\n").length)}
                  className="mt-1 w-full rounded-lg border border-line bg-surface p-2 text-xs"
                />
              </label>
            ))}
          </div>
          {answer.error && <p className="mt-1 text-xs text-danger">{answer.error.message}</p>}
          <div className="mt-2 flex justify-end gap-2">
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
          <dl className="mt-2 grid grid-cols-[12rem_1fr] gap-x-3 gap-y-1 text-xs">
            {PLAN_FIELDS.filter(([name]) => shown[name]).map(([name, label]) => (
              <div key={name} className="contents">
                <dt className="text-muted">{label}</dt>
                <dd className="whitespace-pre-wrap">{shown[name]}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-muted">
            {approval.frozen
              ? `Frozen ${new Date(approval.frozen.at).toLocaleString()} · ${approval.frozen.sha256.slice(0, 12)}`
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
    <div className="rounded-xl border border-research/40 bg-research-soft/40 p-4 text-sm">
      <p className="font-medium text-research">🌐 The agent wants to ask the research helper</p>
      {approval.state === "pending" ? (
        <>
          <p className="mt-1 text-xs text-muted">
            The helper has the internet and sees only this question: not the conversation, files, or data. Check it
            contains no study data before sending. You can edit it.
          </p>
          <textarea
            ref={box}
            value={question}
            dir="ltr"
            onChange={(e) => setQuestion(e.target.value)}
            className="mt-2 w-full resize-none overflow-hidden rounded-lg border border-line bg-surface p-2 font-mono text-xs"
          />
          <p className={`text-right text-xs ${length > 1000 ? "text-danger" : "text-muted"}`}>
            {length} / 1000 characters
          </p>
          {/* Exactly what will be sent, all of it, with anything that isn't plain
              English letters highlighted (look-alike letters could carry data). */}
          <p className="mt-2 text-xs font-medium">What will be sent:</p>
          <p dir="ltr" className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-surface p-2 font-mono text-xs">
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
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-surface p-2 font-mono text-xs">
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
                {approval.answerStatus === "answered" ? "The helper's answer" : "The helper couldn't answer"}
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
