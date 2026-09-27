import { useMutation } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import clsx from "clsx";

import { Button, Chip, Icon } from "@/components/ui";

import { Markdown } from "./Markdown";
import type { AnyPlan, PlanComparison } from "./plan";
import { PlanCard } from "./PlanCard";

export interface Approval {
  kind: "approval";
  id: string;
  approvalKind: "research_helper" | "analysis_plan";
  question: string;
  plan?: AnyPlan;
  /** What the plan is shown against: the approved plan it revises, or what the person sent back. */
  compareTo?: PlanComparison;
  frozen?: { at: string; sha256: string };
  planId?: string;
  /** A later revision replaced this plan. */
  supersededBy?: { planId: string; at: string };
  /** Sent back: the type of analysis the person asked for instead. */
  changeTypeLabel?: string;
  state: "pending" | "approved" | "declined" | "withdrawn";
  sent?: string;
  answer?: string;
  answerStatus?: string;
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
