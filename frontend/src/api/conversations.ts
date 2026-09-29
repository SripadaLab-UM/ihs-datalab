// Conversations: creating and naming them, sending messages, approvals, stopping,
// and what the agent did (events, data accessed).
import type { PlanV2 } from "@/components/chat/plan";

import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Conversation = Schemas["ConversationOut"];
export type Mode = Schemas["ModeOut"];
export type Models = Schemas["ModelsOut"];
export type QueryRecord = Schemas["QueryRecordOut"];
export type Effort = NonNullable<Schemas["NewMessage"]["effort"]>;
export type PlanSchema = Schemas["PlanSchemaOut"];

export const conversationsApi = {
  modes: () => request<Mode[]>("/api/modes"),
  planSchema: () => request<PlanSchema>("/api/plan-schema"),
  models: () => request<Models>("/api/models"),
  conversations: () => request<Conversation[]>("/api/conversations"),
  conversation: (id: string) => request<Conversation>(`/api/conversations/${id}`),
  // No title: DataLab writes one from the first question.
  createConversation: (mode: string, model?: string) =>
    request<Conversation>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ mode, model }),
    }),
  rename: (id: string, title: string) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  // Picks up a turn that failed or was stopped, in the same thread.
  continueTurn: (id: string) => request<Conversation>(`/api/conversations/${id}/continue`, { method: "POST" }),
  // The last turn's rigor review, run again after it couldn't finish.
  rerunReview: (id: string) => request<Conversation>(`/api/conversations/${id}/review`, { method: "POST" }),
  deleteConversation: (id: string) =>
    request<void>(`/api/conversations/${id}`, { method: "DELETE" }),
  send: (id: string, text: string, effort?: Effort) =>
    request<Conversation>(`/api/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ text, effort }),
    }),
  // For a plan sent back, `changeType` asks for another type of analysis instead.
  answerApproval: (id: string, approvalId: string, approve: boolean, question: string, plan?: PlanV2, changeType?: string) =>
    request<void>(`/api/conversations/${id}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ approve, question, plan, change_type: changeType }),
    }),
  setRigorReview: (id: string, on: boolean) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ rigor_review: on }) }),
  // Quick answers. Switching it on switches the rigor review off, and the other way round.
  setExpress: (id: string, on: boolean) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ express: on }) }),
  stop: (id: string) => request<Conversation>(`/api/conversations/${id}/stop`, { method: "POST" }),
  dataAccessed: (id: string) => request<QueryRecord[]>(`/api/conversations/${id}/data-accessed`),
  /** Every event in a conversation (the API returns them 1,000 at a time). */
  allEvents: async (id: string) => {
    const all: { seq: number; type: string; data: Record<string, unknown> }[] = [];
    for (;;) {
      const page = await request<{ seq: number; type: string; data: Record<string, unknown> }[]>(
        `/api/conversations/${id}/events?after=${all.at(-1)?.seq ?? 0}`,
      );
      all.push(...page);
      if (page.length < 1000) return all;
    }
  },
};
