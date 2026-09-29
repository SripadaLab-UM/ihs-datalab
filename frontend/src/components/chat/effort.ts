// How hard the agent thinks for a conversation's next message: the person's
// usual choice, or Express's own while Express is on.
import { useEffect, useState } from "react";

import type { Conversation, Effort } from "@/api/client";

const EFFORT_KEY = "datalab.effort";

/** How hard the agent thinks, remembered in this browser as the default for new messages. */
export function useEffortChoice(): [Effort, (effort: Effort) => void] {
  const [effort, setEffort] = useState<Effort>(() => {
    try {
      const saved = localStorage.getItem(EFFORT_KEY);
      return saved === "low" || saved === "medium" || saved === "high" ? saved : "medium";
    } catch {
      return "medium";
    }
  });
  const choose = (next: Effort) => {
    setEffort(next);
    try {
      localStorage.setItem(EFFORT_KEY, next);
    } catch {
      // Private windows may refuse storage: the choice still holds for this page.
    }
  };
  return [effort, choose];
}

// The effort picked in each conversation while its Express is on.
const EXPRESS_EFFORTS = new Map<string, Effort>();

/**
 * The effort for this conversation's next message: with Express on it's Quick
 * unless the person picks another while it's on; otherwise their usual choice.
 */
export function useExpressEffort(
  conversation: Conversation,
  [chosen, choose]: [Effort, (effort: Effort) => void],
): [Effort, (effort: Effort) => void] {
  const { id, express } = conversation;
  // Kept by conversation for this page (the chat is remade for each one), so
  // A (Express, Thorough), then B, then A again is still Thorough.
  const [, changed] = useState(0);
  useEffect(() => {
    // Switched off: forgotten, so switching it on again starts at Quick.
    if (!express) EXPRESS_EFFORTS.delete(id);
  }, [id, express]);
  if (!express) return [chosen, choose];
  return [
    EXPRESS_EFFORTS.get(id) ?? "low",
    (next) => {
      EXPRESS_EFFORTS.set(id, next);
      changed((n) => n + 1);
    },
  ];
}
