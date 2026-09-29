// The message box under a chat, and what it keeps: a draft per tab, and a
// message that couldn't be sent, put back.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

import { api, type Conversation } from "@/api/client";
import { Button, Icon } from "@/components/ui";

export function Composer({
  conversation,
  running,
  sending,
  error,
  onSend,
  draft,
  note,
  autoFocus,
  placeholder,
  sendLabel,
  compact,
  draftKey,
}: {
  conversation: Conversation;
  running: boolean;
  sending: boolean;
  error?: string;
  onSend: (text: string) => Promise<unknown>;
  draft?: ComposerDraft | null;
  note?: ComposerNote;
  autoFocus?: boolean;
  placeholder?: string;
  sendLabel?: string;
  compact?: boolean;
  draftKey?: string;
}) {
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stop(conversation.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  return (
    <ComposerBox
      running={running}
      sending={sending}
      error={error}
      onSend={onSend}
      draft={draft}
      onStop={() => stop.mutate()}
      stopping={stop.isPending}
      note={note}
      autoFocus={autoFocus}
      placeholder={placeholder}
      sendLabel={sendLabel}
      compact={compact}
      draftKey={draftKey}
    />
  );
}

/** A message that couldn't be sent, back in the box before whatever was typed since. */
export function restored(message: string, typedSince: string): string {
  if (!typedSince.trim()) return message;
  return /\n$/.test(message) ? message + typedSince : `${message}\n${typedSince}`;
}
/** What sits over the message box; given whether a message is being sent, to hold still meanwhile. */
export type ComposerNote = ReactNode | ((sending: boolean) => ReactNode);

/** Text to put back in the message box (a message that couldn't be sent); a new `key` puts it back again. */
export type ComposerDraft = { text: string; key: number };

// The box grows with what's typed (wrapped lines too), up to about eight lines;
// docked, about six, and then it scrolls.
const BOX_MAX = 220;
const COMPACT_BOX_MAX = 150;

/** What's typed in a message box, kept under `key` for this browser tab (sessionStorage) when there is one. */
function useDraftText(key: string | undefined): [string, (next: string | ((current: string) => string)) => void] {
  const [text, setText] = useState(() => {
    if (!key) return "";
    try {
      return sessionStorage.getItem(key) ?? "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    if (!key) return;
    try {
      if (text) sessionStorage.setItem(key, text);
      else sessionStorage.removeItem(key);
    } catch {
      // Storage can be unavailable (a private window): the draft lasts while the box is open.
    }
  }, [key, text]);
  return [text, setText];
}

/** The message box under a chat. It clears as Send is pressed, and gets the text back if `onSend` fails. */
export function ComposerBox({
  running,
  sending,
  error,
  onSend,
  draft,
  onStop,
  stopping = false,
  note,
  autoFocus,
  placeholder = "Ask a question, or say what to do next",
  sendLabel = "Send",
  compact = false,
  draftKey,
}: {
  running: boolean;
  sending: boolean;
  error?: string;
  onSend: (text: string) => Promise<unknown>;
  draft?: ComposerDraft | null;
  onStop?: () => void;
  stopping?: boolean;
  note?: ComposerNote;
  autoFocus?: boolean;
  placeholder?: string;
  sendLabel?: string;
  /** Docked beside a tab: a smaller box whose button shrinks to its icon in a narrow panel. */
  compact?: boolean;
  /** Keep what's typed under this key (sessionStorage), so closing or reopening the chat doesn't lose it. */
  draftKey?: string;
}) {
  const [text, setText] = useDraftText(draftKey);
  // A message that couldn't be sent comes back, before anything typed meanwhile.
  useEffect(() => {
    if (draft) setText((current) => restored(draft.text, current));
  }, [draft, setText]);
  const box = useRef<HTMLTextAreaElement>(null);
  const max = compact ? COMPACT_BOX_MAX : BOX_MAX;
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, max)}px`;
    // Past its most, it scrolls within itself: the messages above keep their room.
    element.style.overflowY = element.scrollHeight > max ? "auto" : "hidden";
  }, [text, max]);

  const submit = () => {
    const typed = text;
    const message = typed.trim();
    if (!message || running || sending) return;
    // Cleared at once: the message shows in the chat. If it fails, it comes back
    // here as it was typed, before anything typed since, and the error shows above.
    setText("");
    onSend(message).catch(() => setText((current) => restored(typed, current)));
  };

  // Docked, the button's label shows only where the panel has room for it and the box.
  const label = (words: string) => (compact ? <span className="hidden @[20rem]:inline">{words}</span> : words);
  return (
    <footer data-tour="composer" className={clsx("shrink-0", compact ? "@container px-3 pt-1.5 pb-3" : "px-4 pt-2 pb-6 sm:px-8")}>
      <div className={clsx(!compact && "mx-auto max-w-[48rem] 2xl:max-w-[54rem]")}>
        {error && <p className="mb-2 text-sm text-danger">{error}</p>}
        {typeof note === "function" ? note(sending) : note}
        {/* The hint sits under the box, so the box itself asks a plain question. */}
        <div
          data-testid="composer-box"
          className={clsx(
            "flex min-w-0 items-end gap-2 rounded-[4px] border border-edge bg-field transition-colors focus-within:border-ink focus-within:shadow-[0_0_0_1px_var(--color-ink)]",
            compact ? "p-1.5 pl-2.5" : "p-2 pl-3",
          )}
        >
          <textarea
            ref={box}
            autoFocus={autoFocus}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            aria-label="Your question or instruction"
            placeholder={
              running
                ? compact
                  ? "Working… Stop it, or wait"
                  : "The agent is working. You can stop it, or wait to ask more."
                : placeholder
            }
            className={clsx(
              "min-w-0 flex-1 resize-none bg-transparent font-sans leading-relaxed outline-none placeholder:text-faint",
              compact ? "min-h-8 py-1 text-[14px] placeholder:truncate" : "min-h-10 py-1.5 text-[15px]",
            )}
          />
          {running ? (
            <Button
              variant="secondary"
              onClick={onStop}
              disabled={stopping || !onStop}
              aria-label={compact ? "Stop" : undefined}
              className={clsx(compact && "shrink-0 px-2 @[20rem]:px-3")}
            >
              <Icon name="stop" size={14} /> {label("Stop")}
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={submit}
              disabled={!text.trim() || sending}
              aria-label={compact ? sendLabel : undefined}
              title={compact ? sendLabel : undefined}
              className={clsx(compact && "shrink-0 px-2 @[20rem]:px-3")}
            >
              <Icon name="send" size={14} /> {label(sendLabel)}
            </Button>
          )}
        </div>
        <p className="mt-1 px-1 font-sans text-[11.5px] text-faint">
          {compact
            ? "Enter to send · Shift+Enter for a new line"
            : "Enter to send · Shift+Enter for a new line · the agent shows its steps as it works"}
        </p>
      </div>
    </footer>
  );
}
