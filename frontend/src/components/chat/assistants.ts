// The chats docked beside a tab (SQL Playground, Workflows, Pipelines, Knowledge):
// what each is called and says about itself in its compact presentation. The
// Workspace's own chat has none of this: it keeps its full empty state.
import { createContext } from "react";

import type { AnyIcon } from "@/components/ui/Icon";

export type AssistantId = "sql" | "workflows" | "pipelines" | "knowledge";

export interface Assistant {
  /** A one-line title over the chat. */
  title: string;
  /** One sentence under it, before the first message. */
  description: string;
  /** The message box's wording: short, so it never wraps beside the button. */
  placeholder: string;
  /** What this tab's assistant does, first in its "What it can do". */
  can: [AnyIcon, string][];
}

export const ASSISTANTS: Record<AssistantId, Assistant> = {
  sql: {
    title: "SQL assistant",
    description: "Describes, writes and revises the query in the editor. You review it and run it.",
    placeholder: "Describe the data you want",
    can: [
      ["search", "Finds the tables and checks every column it uses in the catalog"],
      ["pen", "Puts one query in the editor, with its bind values; it never runs it for you"],
    ],
  },
  workflows: {
    title: "Workflow assistant",
    description: "Creates or changes the workflow you have open. You review, test and save it.",
    placeholder: "Ask for a change or a new check",
    can: [
      ["pen", "Drafts or edits a workflow file in three stages: Extract, Process & QC, Deliver"],
      ["check", "Checks its draft with DataLab's workflow check; it never saves, shares or runs it"],
    ],
  },
  pipelines: {
    title: "Pipelines assistant",
    description: "Explains, edits and tests the pipelines code. Its changes come back here to review.",
    placeholder: "Ask about or change the code",
    can: [
      ["code", "Reads and edits its own copy of the pipelines repo, and runs the package's tests"],
      ["check", "Its changes are a proposal you review, test and save; it never saves them"],
    ],
  },
  knowledge: {
    title: "Knowledge assistant",
    description: "Explains the open page, or proposes improvements for you to review.",
    placeholder: "Ask about or improve this page",
    can: [
      ["book", "Reads the knowledge base, and drafts pages and lab skills in its own copy"],
      ["check", "Its edits come back as proposed changes you review; it never saves them"],
    ],
  },
};

/** Whether the chat is docked beside a tab (compact) or the Workspace's own (full). */
export const CompactContext = createContext(false);

/** The composer's draft for a docked chat, kept for this browser tab: one per
 *  conversation, so New chat starts with an empty box ("new" before it starts). */
export const draftKeyOf = (assistant: AssistantId, conversationId?: string) =>
  `datalab:${assistant}:chat-message:${conversationId ?? "new"}`;
