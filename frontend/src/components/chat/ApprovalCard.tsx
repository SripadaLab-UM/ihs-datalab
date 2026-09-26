import { useMutation } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { Button } from "@/components/ui";

import { Markdown } from "./Markdown";

export interface Approval {
  kind: "approval";
  id: string;
  question: string;
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
