// A panel shown over the page on narrow screens (a drawer, a docked chat): while
// it is, focus moves into it and stays there, Escape closes it, and focus goes
// back where it was. The same rules as Modal, for panels that are only
// sometimes overlays.
import { type RefObject, useEffect, useRef, useState } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

function focusables(box: HTMLElement): HTMLElement[] {
  return [...box.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.checkVisibility?.() ?? true);
}

export function useOverlay(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onClose: () => void,
  /** Where focus goes on close when what had it is gone (a button shown only while it's closed). */
  returnTo?: () => HTMLElement | null,
) {
  const close = useRef(onClose);
  close.current = onClose;
  const back = useRef(returnTo);
  back.current = returnTo;
  useEffect(() => {
    const box = ref.current;
    if (!active || !box) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (focusables(box)[0] ?? box).focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables(box);
      if (items.length === 0) return event.preventDefault();
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    box.addEventListener("keydown", onKey);
    return () => {
      box.removeEventListener("keydown", onKey);
      if (previous?.isConnected && previous !== document.body) previous.focus();
      else back.current?.()?.focus();
    };
  }, [ref, active]);
}

/** Whether a media query matches now, following changes. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window !== "undefined" && window.matchMedia?.(query).matches) || false);
  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media?.addEventListener) return;
    const update = () => setMatches(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}
