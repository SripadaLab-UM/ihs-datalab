import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { use, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "@/api/client";
import { commitUrl, knowledgeApi } from "@/api/knowledge";
import { Button, Chip, Icon, Modal } from "@/components/ui";

import { CompactContext } from "./assistants";
import { Markdown } from "./Markdown";
import { ShowQueryContext } from "./provenance";
import { HOVER_TITLE, Marker } from "./Story";
import type { KbSuggestionItem } from "./transcript";

/** Where the Knowledge tab opens an edit: the page, the edit, and the view to start in. */
export function editLink(path: string, editId: string, view: "edit" | "review" = "review"): string {
  return `/knowledge/${path}?edit=${encodeURIComponent(editId)}&view=${view}`;
}

/** The note for practice DataLab, which has no lab knowledge base. */
export const REAL_ONLY = "Available on the real DataLab";

/** Whether a card is open, kept for the session (a reload keeps it; a new tab starts folded). */
const OPEN_KEY = (id: string) => `datalab:kb-suggestion-open:${id}`;
function readOpen(id: string): boolean {
  try {
    return sessionStorage.getItem(OPEN_KEY(id)) === "1";
  } catch {
    return false;
  }
}
function keepOpen(id: string, open: boolean) {
  try {
    if (open) sessionStorage.setItem(OPEN_KEY(id), "1");
    else sessionStorage.removeItem(OPEN_KEY(id));
  } catch {
    // Storage may be blocked; the card still folds and opens.
  }
}

/**
 * A Suggested Knowledge update, under the answer that made it. Folded to a
 * row of its own like the chat's other rows ("+" opens it): what it is, its
 * title and page, how it stands, and Accept as proposal (or, once acted on,
 * what came of it). Open, it shows the text, why, and the queries behind it.
 * Accept makes it a draft edit of that page, to review and Save & share in
 * the Knowledge tab (Edit first opens that editor at once); Dismiss sets it
 * aside. Once acted on, it folds back to its row. Nothing reaches the
 * knowledge base from here.
 */
export function KbSuggestionCard({ suggestion, conversationId }: { suggestion: KbSuggestionItem; conversationId: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const openQuery = use(ShowQueryContext);
  const compact = use(CompactContext);
  const detailId = useId();
  const card = useRef<HTMLElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const outcome = useRef<HTMLAnchorElement>(null);
  const [open, setOpenState] = useState(() => readOpen(suggestion.id));
  const setOpen = (next: boolean) => {
    setOpenState(next);
    keepOpen(suggestion.id, next);
  };
  const status = useQuery({ queryKey: ["knowledge-status"], queryFn: knowledgeApi.status });
  const available = status.data?.available ?? false;
  const pages = useQuery({ queryKey: ["kb-pages"], queryFn: knowledgeApi.pages, enabled: available });
  const exists = pages.data?.pages.some((p) => p.path === suggestion.page);
  // Once accepted, how its edit stands (a draft on this computer, or shared).
  const edit = useQuery({
    queryKey: ["kb-edit-by-id", suggestion.editId],
    queryFn: () => knowledgeApi.getEdit(suggestion.editId!),
    enabled: Boolean(suggestion.editId) && available,
  });
  const accept = useMutation({
    mutationFn: (_then: "stay" | "edit") => knowledgeApi.acceptSuggestion(conversationId, suggestion.id),
    onSuccess: (made, then) => {
      queryClient.setQueryData(["kb-edit-by-id", made.id], made);
      queryClient.invalidateQueries({ queryKey: ["kb-edits"] });
      if (then === "edit") navigate(editLink(made.path, made.id, "edit"));
    },
  });
  const dismiss = useMutation({ mutationFn: () => knowledgeApi.dismissSuggestion(conversationId, suggestion.id) });
  const busy = accept.isPending || dismiss.isPending;
  const editId = suggestion.editId ?? accept.data?.id ?? null;
  const state = accept.data ? "accepted" : dismiss.data?.status === "dismissed" ? "dismissed" : suggestion.status;
  const shared = edit.data?.status === "saved" ? edit.data : null;
  const commit = shared?.commit ? commitUrl(status.data?.name, shared.commit) : null;
  const where = available && pages.data ? (exists ? "adds a section to this page" : "a new page (a draft)") : null;
  const chip =
    state === "open" ? (
      <Chip tone="you">pending</Chip>
    ) : state === "dismissed" ? (
      <Chip>dismissed</Chip>
    ) : shared ? (
      <Chip tone="good">shared</Chip>
    ) : accept.variables === "edit" ? (
      <Chip tone="you">accepted · editing</Chip>
    ) : (
      <Chip tone="you">accepted as a draft</Chip>
    );

  // Acted on (here, or in another window): fold back to the row, with focus on
  // what came of it rather than lost with the button that was pressed.
  const acted = state !== "open";
  const actedHere = accept.isSuccess || dismiss.isSuccess;
  const wasOpen = useRef(!acted);
  useEffect(() => {
    if (!acted || !wasOpen.current) return;
    wasOpen.current = false;
    setOpenState(false);
    keepOpen(suggestion.id, false);
    // Only when it was acted on here: an update from elsewhere doesn't move focus.
    if (!actedHere) return;
    const active = document.activeElement;
    if (active && active !== document.body && !card.current?.contains(active)) return;
    (outcome.current ?? toggle.current)?.focus();
  }, [acted, actedHere, suggestion.id]);

  // The person's own: asked for with Remember in Knowledge (or the older form).
  const label = suggestion.requested || suggestion.by === "person" ? "Your Knowledge update" : "Suggested Knowledge update";
  const linkClass = "text-ink underline decoration-faint underline-offset-4 hover:decoration-ink";
  const commitLink = shared?.commit ? (
    commit ? (
      <a ref={outcome} href={commit} target="_blank" rel="noopener noreferrer" className={clsx(linkClass, "font-mono")}>
        {shared.commit.slice(0, 7)}
      </a>
    ) : (
      <span className="font-mono">{shared.commit.slice(0, 7)}</span>
    )
  ) : null;

  return (
    <section
      ref={card}
      aria-label={`${label}: ${suggestion.title}`}
      className={clsx("min-w-0 border-l text-[14px]", compact ? "pl-2" : "pl-3", state === "open" ? "border-you" : "border-line")}
    >
      {/* The row: "+" opens it; Accept (or what came of it) stays beside it. */}
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <button
          ref={toggle}
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls={detailId}
          // Room for the title first: on a narrow screen, Accept wraps below it.
          className={clsx("group flex min-w-0 flex-1 basis-64 items-start text-left", compact ? "gap-2 py-1.5" : "gap-3 py-2")}
        >
          <Marker tone="done" open={open} />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Icon name="book" size={13} className="shrink-0 text-faint" />
              <span className="dl-label">{label}</span>
              {chip}
            </span>
            <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              <span className={clsx("font-serif leading-snug text-ink", HOVER_TITLE, compact ? "text-[15px]" : "text-[16px]")}>
                {suggestion.title}
              </span>
              <span className={clsx("font-mono text-[12px] text-muted", compact ? "min-w-0 break-all" : "")}>{suggestion.page}</span>
              {where && !compact && <span className="font-sans text-[12.5px] text-muted">{where}</span>}
            </span>
          </span>
        </button>
        <div className={clsx("flex flex-wrap items-center gap-x-2 gap-y-1 font-sans text-[12.5px]", compact ? "w-full py-1 pl-[15px]" : "ml-auto py-2")}>
          {state === "open" && !open && (
            <>
              {!available && status.data && <span className="text-faint">{REAL_ONLY}</span>}
              <Button
                variant="primary"
                className="px-2.5 py-1 text-[12.5px]"
                onClick={() => accept.mutate("stay")}
                disabled={busy || !available}
                title={available ? "Make it a draft edit to review in Knowledge" : REAL_ONLY}
              >
                Accept as proposal
              </Button>
            </>
          )}
          {state === "accepted" && editId && !shared && (
            <Link ref={outcome} to={editLink(suggestion.page, editId)} className={linkClass}>
              Review in Knowledge →
            </Link>
          )}
          {state === "accepted" && shared && (
            <span className="text-data">Shared to GitHub{commitLink && <> · commit {commitLink}</>}</span>
          )}
        </div>
      </div>
      {(accept.error || dismiss.error) && (
        <p role="alert" className="pb-2 font-sans text-[13px] text-danger">
          {(accept.error ?? dismiss.error)?.message}
        </p>
      )}

      <div id={detailId}>
        {open && (
          <div className={clsx("flex flex-col gap-3 pb-3", compact ? "pl-[15px]" : "pl-[19px]")}>
            {where && compact && <p className="font-sans text-[12.5px] text-muted">{where}</p>}
            {state === "dismissed" && <p className="font-sans text-[13px] text-muted">Dismissed: it wasn't added to the knowledge base.</p>}
            <div className="rounded-[3px] border border-line bg-sunken px-3 py-2 text-[14px]">
              <Markdown text={suggestion.text} />
            </div>
            {suggestion.reason && (
              <p className="font-sans text-[13px] text-muted">
                <span className="text-ink">Why keep it: </span>
                {suggestion.reason}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-sans text-[12.5px] text-muted">
              <span className="dl-label">Evidence</span>
              {suggestion.evidence.length === 0 && <span>None from this conversation: you asked to keep it</span>}
              {suggestion.evidence.map((e) => (
                <span key={e.query_id} className="inline-flex items-center gap-1">
                  {openQuery ? (
                    <button
                      type="button"
                      onClick={() => openQuery(e.query_id)}
                      title={`Query ${e.query_id}: show it in the Queries tab`}
                      className="inline-flex items-center gap-1 rounded-[3px] border border-line px-1.5 py-px text-ink hover:border-ink"
                    >
                      <Icon name="db" size={12} /> {e.tables.map((t) => t.replace(/^IHS_\d{4}\./, "")).join(", ") || "Query"}
                      <span className="font-mono text-[11px] text-faint">{e.query_id}</span>
                    </button>
                  ) : (
                    <span className="font-mono text-[12px]">{e.query_id}</span>
                  )}
                </span>
              ))}
              {suggestion.turn > 0 && (
                <button
                  type="button"
                  onClick={() => card.current?.closest("article")?.scrollIntoView({ block: "start", behavior: "smooth" })}
                  className="underline decoration-faint underline-offset-4 hover:text-ink"
                >
                  From question {suggestion.turn}
                </button>
              )}
            </div>

            {state === "open" && (
              <div className="flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <Button variant="ghost" onClick={() => dismiss.mutate()} disabled={busy}>
                    Dismiss
                  </Button>
                  <Button onClick={() => accept.mutate("edit")} disabled={busy || !available} title={available ? undefined : REAL_ONLY}>
                    <Icon name="pen" size={13} /> Edit first
                  </Button>
                  <Button variant="primary" onClick={() => accept.mutate("stay")} disabled={busy || !available} title={available ? undefined : REAL_ONLY}>
                    Accept as proposal
                  </Button>
                </div>
                <p className="text-right font-sans text-[12px] text-faint">
                  {available
                    ? "Accepting makes it a draft edit on this computer. Nothing goes to GitHub until you review it and Save & share."
                    : `${REAL_ONLY}: practice DataLab has no lab knowledge base to add it to.`}
                </p>
              </div>
            )}
            {state === "accepted" && editId && !shared && (
              <p className="font-sans text-[13px] text-muted">A draft of {suggestion.page}, on this computer: not shared yet.</p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * "Remember in Knowledge": the person says, in their own words, what the lab
 * knowledge base should keep from this conversation, and the conversation's
 * agent does the rest. What they write is sent as a message (marked as one,
 * so DataLab asks the agent for a Knowledge update): the agent finds the page,
 * writes it up and cites the queries here that show it, if any. DataLab checks
 * it as it checks every suggestion and makes it a draft edit at once, to review
 * and Save & share in the Knowledge tab. Nothing is shared from here.
 */
export function RememberButton({
  mode,
  onSend,
  busy,
}: {
  /** The conversation's mode: only those whose agent can suggest Knowledge updates offer it. */
  mode: string;
  /** Send the text as a Remember in Knowledge message (Chat's own send, so it shows like any other). */
  onSend: (text: string) => Promise<unknown>;
  /** The agent is working: one message at a time. */
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const status = useQuery({ queryKey: ["knowledge-status"], queryFn: knowledgeApi.status });
  const available = status.data?.available ?? false;
  if (!modes.data?.find((m) => m.id === mode)?.remember) return null;
  return (
    <>
      <Button
        variant="ghost"
        className="px-2 text-[13px]"
        onClick={() => setOpen(true)}
        disabled={!available || busy}
        title={
          !available
            ? `${REAL_ONLY}: practice DataLab has no lab knowledge base`
            : busy
              ? "The agent is working: ask once it has answered"
              : "Tell the agent what the lab knowledge base should keep from this conversation"
        }
      >
        <Icon name="book" size={13} /> Remember in Knowledge
      </Button>
      {open && <RememberForm onSend={onSend} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The longest description: a Knowledge update's text may be 4,000 characters. */
const MAX_REMEMBER = 4000;

function RememberForm({ onSend, onClose }: { onSend: (text: string) => Promise<unknown>; onClose: () => void }) {
  const fieldId = useId();
  const [text, setText] = useState("");
  const ask = useMutation({ mutationFn: () => onSend(text.trim()), onSuccess: onClose });
  const ready = text.trim().length > 0 && !ask.isPending;
  return (
    <Modal title="Remember in Knowledge" onClose={onClose}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) ask.mutate();
        }}
      >
        <p className="font-sans text-[13px] text-muted">
          Say what the lab knowledge base should keep from this conversation: a quirk in the data, what a column really
          holds, a definition, a caveat. The agent finds the page, writes it up, and cites the queries here that show it.
        </p>
        <label htmlFor={fieldId} className="dl-label">
          What should DataLab remember?
        </label>
        <textarea
          id={fieldId}
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          maxLength={MAX_REMEMBER}
          placeholder="For example: a step count of 0 means the tracker wasn't synced that day, not that the intern didn't move."
          className="resize-y rounded-[3px] border border-line bg-field px-2.5 py-1.5 font-sans text-[13.5px] outline-none focus:border-ink"
        />
        {ask.error && (
          <p role="alert" className="font-sans text-[13px] text-danger">
            {ask.error.message}
          </p>
        )}
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!ready}>
              {ask.isPending ? "Sending…" : "Ask the agent"}
            </Button>
          </div>
          <p className="font-sans text-[12px] text-faint">
            It's checked for participant data, then kept as a draft on this computer. You review it and Save & share it in
            the Knowledge tab. Never participant-level data or small counts.
          </p>
        </div>
      </form>
    </Modal>
  );
}
