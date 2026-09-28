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
  /** What gets focus on open (a chat's message box), when not its first control. */
  initial?: (box: HTMLElement) => HTMLElement | null,
) {
  const close = useRef(onClose);
  close.current = onClose;
  const back = useRef(returnTo);
  back.current = returnTo;
  const first = useRef(initial);
  first.current = initial;
  useEffect(() => {
    const box = ref.current;
    if (!active || !box) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (first.current?.(box) ?? focusables(box)[0] ?? box).focus();
    // Marked, so its own role=dialog isn't taken for another dialog open over it.
    box.setAttribute("data-overlay-self", "");
    // Escape, once everything inside has had its turn: a glossary tip or a
    // menu in the drawer closes first (and says so with preventDefault), and
    // a dialog open over the drawer closes before it.
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (document.querySelector("[role=dialog]:not([data-overlay-self])")) return;
      event.preventDefault();
      close.current();
    };
    const onKey = (event: KeyboardEvent) => {
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
    window.addEventListener("keydown", onEscape);
    return () => {
      box.removeEventListener("keydown", onKey);
      window.removeEventListener("keydown", onEscape);
      box.removeAttribute("data-overlay-self");
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

/**
 * Escape closes a panel while it's shown over the page (a drawer or docked
 * chat on a narrow window), unless a dialog is open on top of it: the
 * keyboard's way to do what a click on the dimmed page around it does.
 */
export function useEscapeToClose(active: boolean, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("[role=dialog]")) close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
}
