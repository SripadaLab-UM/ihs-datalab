import { useQuery } from "@tanstack/react-query";
import { createContext, type ReactNode, use, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router";

import { api } from "@/api/client";
import { Button } from "@/components/ui";
import { pageBySlug, slug } from "@/lib/guide";

import { GuideMarkdown } from "./GuideMarkdown";

export interface TourStep {
  id: string;
  title: string;
  text: string;
}

/** The tour's steps: each `##` in docs/guide/tour.md. */
export function tourSteps(): TourStep[] {
  const body = pageBySlug("tour")?.body ?? "";
  const parts = body.split(/^## (.+)$/m);
  const steps: TourStep[] = [];
  for (let i = 1; i < parts.length; i += 2) {
    steps.push({ id: slug(parts[i]), title: parts[i].trim(), text: parts[i + 1].trim() });
  }
  return steps;
}

/**
 * What each step points at, by its heading's anchor: selectors tried in
 * order, the first one on screen wins. A step whose place isn't on screen
 * (no conversation open yet) is still read; it just points at nothing.
 */
export const TOUR_ANCHORS: Record<string, string[]> = {
  "ask-a-question": ["[data-tour=composer]", "[data-tour=new-conversation]"],
  "watch-the-steps": ["[data-tour=steps]", "[data-tour=composer]"],
  "open-a-step": ["[data-tour=steps] button[aria-expanded]", "[data-tour=how-made]"],
  "read-the-answer-its-trace-and-review": ["[data-tour=answer]"],
  "find-the-outputs-and-export": ["[data-tour=outputs]", "[data-tour=export]"],
};

const SEEN_KEY = "datalab.tour.seen";
// If this browser won't keep it (a private window, say), it's remembered for this page at least.
let seenHere = false;

export function tourSeen(): boolean {
  if (seenHere) return true;
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function markTourSeen() {
  seenHere = true;
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    // Storage refused: the tour won't start again on this page, and may on the next.
  }
}

/** For tests: forget that the tour was seen on this page. */
export function resetTourMemory() {
  seenHere = false;
}

interface TourControl {
  open: boolean;
  start: () => void;
}

const TourContext = createContext<TourControl>({ open: false, start: () => undefined });

export function useTour(): TourControl {
  return use(TourContext);
}

/**
 * The first-run tour. It starts by itself once, in the Workspace of the
 * practice DataLab, and again whenever Help's "Take the tour" asks.
 */
export function TourProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const { pathname } = useLocation();
  const practice = health.data?.profile === "practice";
  useEffect(() => {
    if (practice && pathname.startsWith("/workspace") && !tourSeen()) setOpen(true);
  }, [practice, pathname]);
  const start = useCallback(() => setOpen(true), []);
  const close = useCallback(() => {
    markTourSeen();
    setOpen(false);
  }, []);
  return (
    <TourContext value={{ open, start }}>
      {children}
      {open && <TourCard onClose={close} />}
    </TourContext>
  );
}

function findAnchor(stepId: string): HTMLElement | null {
  for (const selector of TOUR_ANCHORS[stepId] ?? []) {
    const found = [...document.querySelectorAll<HTMLElement>(selector)].find((el) => el.checkVisibility?.() ?? true);
    if (found) return found;
  }
  return null;
}

/** One step at a time, beside the page (not over it): you can try each step as you read it. */
export function TourCard({ onClose }: { onClose: () => void }) {
  const steps = useMemo(tourSteps, []);
  const [index, setIndex] = useState(0);
  const step = steps[index];
  const titleId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const last = index === steps.length - 1;

  // Focus on the step's title, so it's read out, whenever the step changes.
  useEffect(() => {
    heading.current?.focus();
  }, [index]);

  // The place on screen this step is about, outlined while it's shown.
  useEffect(() => {
    if (!step) return;
    const anchor = findAnchor(step.id);
    anchor?.setAttribute("data-tour-on", "");
    anchor?.scrollIntoView?.({ block: "nearest" });
    return () => anchor?.removeAttribute("data-tour-on");
  }, [step]);

  if (!step) return null;
  return (
    <section
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
      className="dl-in fixed right-4 bottom-4 z-40 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-3 rounded-[4px] border border-line border-t-2 border-t-ink bg-surface px-5 pt-4 pb-4 shadow-[0_24px_60px_-20px_rgba(0,0,0,0.35)]"
    >
      <p className="dl-label">
        Tour · {index + 1} of {steps.length}
      </p>
      <h2 ref={heading} id={titleId} tabIndex={-1} className="font-serif text-[22px] leading-tight outline-none">
        {step.title}
      </h2>
      <div className="[&_.prose-datalab]:text-[16px]">
        <GuideMarkdown page={{ slug: "tour" }} text={step.text} onNavigate={onClose} />
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="mr-auto">
          {!last && (
            <button
              type="button"
              onClick={onClose}
              className="font-sans text-[13px] text-muted underline decoration-faint underline-offset-4 hover:text-ink"
            >
              Skip the tour
            </button>
          )}
        </span>
        {index > 0 && <Button onClick={() => setIndex(index - 1)}>Back</Button>}
        {last ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <Button variant="primary" onClick={() => setIndex(index + 1)}>
            Next
          </Button>
        )}
      </div>
    </section>
  );
}
