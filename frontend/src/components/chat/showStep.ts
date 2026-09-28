// Asking the chat to show one step of the agent's work (a command it ran),
// from beside it: the Code tab's inline code links here. The turn it's in
// opens "How this answer was made" at that step, opens it, and scrolls to it.
import { createContext } from "react";

export const SHOW_STEP = "datalab:show-step";

/** Show the step with this key (activity.ts: "cmd-<id>" for a command). */
export function showStep(key: string): void {
  window.dispatchEvent(new CustomEvent(SHOW_STEP, { detail: { key } }));
}

/** The step asked for, with a count so the same step asked again opens again. */
export interface ShownStep {
  key: string;
  n: number;
}

export const ShownStepContext = createContext<ShownStep | null>(null);

/** The element id of a step's row, to scroll to. */
export const stepElementId = (key: string) => `step-${key}`;
