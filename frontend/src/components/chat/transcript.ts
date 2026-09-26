// Turns a conversation's event log into what the chat shows.
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
  | { kind: "notice"; tone: "error" | "info"; text: string };

export interface Turn {
  userText: string;
  items: Item[];
  status: "running" | "completed" | "interrupted" | "failed";
}

const MAX_COMMAND_OUTPUT = 20_000;

export function buildTranscript(events: ConversationEvent[]): Turn[] {
  const turns: Turn[] = [];
  let turn: Turn | undefined;
  const byId = new Map<string, Item>();

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
    switch (event.type) {
      case "user_message":
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
        add({ kind: "notice", tone: "info", text: text(data.text) });
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
      case "stop_requested":
        add({ kind: "notice", tone: "info", text: "Stopping…" });
        break;
      case "turn_finished": {
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
