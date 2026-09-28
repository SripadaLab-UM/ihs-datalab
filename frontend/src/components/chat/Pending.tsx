import { useEffect, useState } from "react";

/** A message sent but not yet in the conversation's events: shown at once, so a click never seems to do nothing. */
export interface PendingMessage {
  /** What the person typed or picked (not the context sent along with it). */
  text: string;
  /** The last event seen when it was sent: the message is in once an event after this says so. */
  after: number;
}

/** After this long without DataLab's word, the line says why it's taking a while. */
export const SLOW_MS = 3000;

/**
 * What a message on its way says, over time. Until DataLab answers, all that's
 * known is that it's being sent; after a few seconds, the line says what DataLab
 * does before it answers (makes room for and prepares the agent's workspace),
 * which takes longest for a conversation's first message. It never claims more.
 */
export function useSendingLine(active: boolean): string {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    setSlow(false);
    if (!active) return;
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    return () => clearTimeout(timer);
  }, [active]);
  return slow ? "DataLab is preparing the workspace before the agent starts…" : "Sending…";
}

/** The "Starting…" line under a message on its way, for screen readers too. */
export function SendingLine({ className = "" }: { className?: string }) {
  const line = useSendingLine(true);
  return (
    <p role="status" aria-live="polite" className={`flex items-center gap-2.5 font-sans text-[14px] text-muted ${className}`}>
      <span aria-hidden className="dl-breathe size-2 shrink-0 rounded-full bg-ink" />
      {line}
    </p>
  );
}
