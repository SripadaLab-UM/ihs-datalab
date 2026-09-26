// Turns a conversation's event log into what the chat shows.
import type { Approval } from "./ApprovalCard";
//
// The event log (see backend sessions/runtime.py) is a flat, append-only list:
// user messages, streamed answer text, reasoning, commands, tool calls, and
// turn status. This groups it into turns and items, in order.

export interface ConversationEvent {
  seq: number;
  type: string;
  data: Record<string, unknown>;
}

export type Item =
  | { kind: "message"; id: string; phase: "commentary" | "final_answer" | null; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "command"; id: string; command: string; output: string; exitCode: number | null; status: string }
  | { kind: "tool"; id: string; tool: string; server: string; status: string; arguments: unknown; error: string | null }
  | { kind: "files"; paths: string[] }
  | Approval
  | { kind: "review"; text: string; status: "running" | "done" | "stopped" | "failed" }
  | { kind: "notice"; tone: "error" | "info"; text: string };

export interface Turn {
  userText: string;
  items: Item[];
  status: "running" | "completed" | "interrupted" | "failed";
  /** Numbers in the answer that nothing the turn produced contains. */
  trace?: { numbers: number; untraced: string[] };
}

const MAX_COMMAND_OUTPUT = 20_000;
const REVIEW_TURN_EVENTS = new Set([
  "answer_started", "answer_delta", "answer", "turn_started", "turn_finished", "reasoning_delta",
  "command_started", "command_output", "command_finished", "tool_call", "files_changed", "web_search",
]);

export function buildTranscript(events: ConversationEvent[]): Turn[] {
  const turns: Turn[] = [];
  let turn: Turn | undefined;
  const byId = new Map<string, Item>();
  const approvals = new Map<string, Approval>();
  // During a rigor review, Codex's messages are the review, not the answer.
  let review: Extract<Item, { kind: "review" }> | undefined;

  const current = (): Turn => {
    if (!turn) {
      turn = { userText: "", items: [], status: "running" };
      turns.push(turn);
    }
    return turn;
  };
  const add = (item: Item) => current().items.push(item);
  const text = (value: unknown) => (typeof value === "string" ? value : "");

  for (const event of events) {
    const data = event.data;
    const id = text(data.id);
    // The review turn's own work isn't the answer's: its messages, commands,
    // and tool calls are left out; its end closes the review.
    if (review?.status === "running" && REVIEW_TURN_EVENTS.has(event.type)) {
      if (event.type === "turn_finished") {
        review.status = data.status === "interrupted" ? "stopped" : review.text && data.status === "completed" ? "done" : "failed";
      }
      continue;
    }
    switch (event.type) {
      case "review_started":
        review = { kind: "review", text: "", status: "running" };
        add(review);
        break;
      case "review":
        if (review) review.text = text(data.text);
        break;
      case "review_finished":
        if (review) {
          review.status =
            data.status === "interrupted" ? "stopped" : data.status === "completed" && review.text ? "done" : "failed";
        }
        review = undefined;
        break;
      case "trace":
        current().trace = {
          numbers: Number(data.numbers) || 0,
          untraced: Array.isArray(data.untraced) ? data.untraced.map(String) : [],
        };
        break;
      case "plan_approved": {
        const item = approvals.get(text(data.approval));
        if (item) {
          item.plan = (data.plan as Record<string, string>) ?? item.plan;
          item.frozen = { at: text(data.approved_at), sha256: text(data.sha256) };
        }
        break;
      }
      case "user_message":
        // A review that never said it finished (DataLab stopped) is over now.
        if (review?.status === "running") review.status = "failed";
        review = undefined;
        turn = { userText: text(data.text), items: [], status: "running" };
        turns.push(turn);
        byId.clear();
        break;
      case "answer_started": {
        const item: Item = { kind: "message", id, phase: (data.phase as never) ?? null, text: "" };
        byId.set(id, item);
        add(item);
        break;
      }
      case "answer_delta": {
        let item = byId.get(id);
        if (!item) {
          item = { kind: "message", id, phase: null, text: "" };
          byId.set(id, item);
          add(item);
        }
        if (item.kind === "message") item.text += text(data.text);
        break;
      }
      case "answer": {
        const item = byId.get(id);
        if (item?.kind === "message") {
          item.text = text(data.text) || item.text;
          item.phase = (data.phase as never) ?? item.phase;
        } else {
          const created: Item = { kind: "message", id, phase: (data.phase as never) ?? null, text: text(data.text) };
          byId.set(id, created);
          add(created);
        }
        break;
      }
      case "reasoning_delta": {
        const items = current().items;
        const last = items[items.length - 1];
        if (last?.kind === "reasoning") last.text += text(data.text);
        else add({ kind: "reasoning", text: text(data.text) });
        break;
      }
      case "command_started": {
        const item: Item = { kind: "command", id, command: text(data.command), output: "", exitCode: null, status: "running" };
        byId.set(id, item);
        add(item);
        break;
      }
      case "command_output": {
        const item = byId.get(id);
        if (item?.kind === "command") item.output = (item.output + text(data.text)).slice(-MAX_COMMAND_OUTPUT);
        break;
      }
      case "command_finished": {
        const item = byId.get(id);
        if (item?.kind === "command") {
          item.exitCode = typeof data.exit_code === "number" ? data.exit_code : null;
          item.status = text(data.status) || "completed";
        }
        break;
      }
      case "tool_call":
        add({
          kind: "tool",
          id,
          tool: text(data.tool),
          server: text(data.server),
          status: text(data.status),
          arguments: data.arguments,
          error: data.error ? JSON.stringify(data.error) : null,
        });
        break;
      case "error":
        add({ kind: "notice", tone: "error", text: text(data.message) || "Something went wrong." });
        break;
      case "notice":
        add({ kind: "notice", tone: data.tone === "error" ? "error" : "info", text: text(data.text) });
        break;
      case "approval_requested": {
        const item: Approval = {
          kind: "approval",
          id,
          approvalKind: data.kind === "analysis_plan" ? "analysis_plan" : "research_helper",
          question: text(data.question),
          plan: (data.plan as Record<string, string> | undefined) ?? undefined,
          state: "pending",
        };
        approvals.set(id, item);
        add(item);
        break;
      }
      case "approval_answered": {
        const item = approvals.get(id);
        if (item) {
          item.state = data.approved ? "approved" : "declined";
          if (data.approved) item.sent = text(data.question);
        }
        break;
      }
      case "approval_withdrawn": {
        const item = approvals.get(id);
        if (item && item.state === "pending") item.state = "withdrawn";
        break;
      }
      case "helper_answered": {
        const item = approvals.get(text(data.approval));
        if (item) {
          item.answer = text(data.answer);
          item.answerStatus = text(data.status);
        }
        break;
      }
      case "input_unavailable":
        add({ kind: "notice", tone: "error", text: `Not attached this time: ${text(data.reason)}.` });
        break;
      case "exported":
        turn = {
          userText: "",
          items: [{ kind: "notice", tone: "info", text: `You exported ${String(data.files)} file(s) to ${text(data.folder)}.` }],
          status: "completed",
        };
        turns.push(turn);
        break;
      case "files_changed": {
        const paths = Array.isArray(data.paths) ? data.paths.filter((p): p is string => typeof p === "string") : [];
        if (paths.length) add({ kind: "files", paths });
        break;
      }
      case "files_restored": {
        // Between turns: shown on its own, after the turn it follows.
        const label = text(data.label).toLowerCase() || "an earlier checkpoint";
        const leftAlone = Array.isArray(data.left_alone) ? data.left_alone.length : 0;
        const notRestored = Array.isArray(data.not_restored) ? data.not_restored.length : 0;
        const notice: Item = data.failed
          ? { kind: "notice", tone: "error", text: `Restoring the files to how they were ${label} failed partway: ${text(data.error)}. The agent will be told.` }
          : {
              kind: "notice",
              tone: "info",
              text:
                `You restored the files to how they were ${label}. The agent will be told on its next turn.` +
                (leftAlone ? ` ${leftAlone} file${leftAlone > 1 ? "s" : ""} couldn't be checkpointed and ${leftAlone > 1 ? "were" : "was"} left as ${leftAlone > 1 ? "they were" : "it was"}.` : "") +
                (notRestored ? ` ${notRestored} file${notRestored > 1 ? "s weren't" : " wasn't"} put back, so as not to replace ${leftAlone > 1 ? "those" : "it"}.` : ""),
            };
        turn = { userText: "", items: [notice], status: "completed" };
        turns.push(turn);
        break;
      }
      case "input_attached":
      case "input_removed": {
        const items = Array.isArray(data.items) ? (data.items as { path?: string }[]) : [];
        const paths = items.map((i) => i.path).filter(Boolean).join(", ");
        const what = event.type === "input_attached" ? `You attached ${paths} (read-only).` : `You removed ${paths}.`;
        turn = { userText: "", items: [{ kind: "notice", tone: "info", text: `${what} The agent will be told on its next turn.` }], status: "completed" };
        turns.push(turn);
        break;
      }
      case "turn_done":
        // DataLab has finished the turn, with its checkpoint and review.
        if (review?.status === "running") review.status = "failed";
        if (turn?.status === "running") turn.status = "completed";
        break;
      case "stop_requested":
        add({ kind: "notice", tone: "info", text: "Stopping…" });
        break;
      case "turn_finished": {
        // A question still waiting when the turn ends can't be answered any more.
        for (const approval of approvals.values()) {
          if (approval.state === "pending") approval.state = "withdrawn";
          if (approval.state === "approved" && !approval.answer) {
            approval.answer = "No answer came back.";
            approval.answerStatus = "failed";
          }
        }
        const status = text(data.status);
        current().status =
          status === "interrupted" || status === "failed" ? status : "completed";
        if (data.error) add({ kind: "notice", tone: "error", text: text(data.error) });
        break;
      }
    }
  }
  return turns;
}

/** The answer to show prominently: the final answer, or the last message so far. */
export function finalAnswer(turn: Turn): string {
  const messages = turn.items.filter((i) => i.kind === "message");
  const final = messages.filter((m) => m.phase === "final_answer");
  const chosen = final[final.length - 1] ?? (turn.status === "running" ? undefined : messages[messages.length - 1]);
  return chosen?.text ?? "";
}
