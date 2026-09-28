import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { use, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, type Conversation } from "@/api/client";
import { commitUrl, knowledgeApi } from "@/api/knowledge";
import { Button, Chip, Icon, Modal } from "@/components/ui";

import { Markdown } from "./Markdown";
import { ShowQueryContext } from "./provenance";
import type { KbSuggestionItem } from "./transcript";

/** Where the Knowledge tab opens an edit: the page, the edit, and the view to start in. */
export function editLink(path: string, editId: string, view: "edit" | "review" = "review"): string {
  return `/knowledge/${path}?edit=${encodeURIComponent(editId)}&view=${view}`;
}

/** The note for practice DataLab, which has no lab knowledge base. */
export const REAL_ONLY = "Available on the real DataLab";

/**
 * A Suggested Knowledge update, under the answer that made it: the page it's
 * for, the text, why, and the queries behind it. Accept makes it a draft edit
 * of that page, to review and Save & share in the Knowledge tab (Edit first
 * opens that editor at once); Dismiss sets it aside. Nothing reaches the
 * knowledge base from here.
 */
export function KbSuggestionCard({ suggestion, conversationId }: { suggestion: KbSuggestionItem; conversationId: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const openQuery = use(ShowQueryContext);
  const card = useRef<HTMLElement>(null);
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

  return (
    <section
      ref={card}
      aria-label={`Suggested Knowledge update: ${suggestion.title}`}
      className="flex flex-col gap-3 rounded-[4px] border border-line bg-surface px-4 py-3"
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Icon name="book" size={14} className="text-muted" />
        <h3 className="dl-label">{suggestion.by === "person" ? "Your Knowledge update" : "Suggested Knowledge update"}</h3>
        {state === "accepted" && <Chip tone={shared ? "good" : "you"}>{shared ? "shared" : "accepted as a proposal"}</Chip>}
        {state === "dismissed" && <Chip>dismissed</Chip>}
      </header>
      <div className="flex flex-col gap-1">
        <p className="font-serif text-[17px] leading-snug">{suggestion.title}</p>
        <p className="flex flex-wrap items-baseline gap-x-2 font-sans text-[12.5px] text-muted">
          <span className="font-mono text-[12.5px] text-ink">{suggestion.page}</span>
          {available && pages.data && <span>{exists ? "adds a section to this page" : "a new page (a draft)"}</span>}
        </p>
      </div>
      {state !== "dismissed" && (
        <div className="rounded-[3px] border border-line bg-sunken px-3 py-2 text-[14px]">
          <Markdown text={suggestion.text} />
        </div>
      )}
      {suggestion.reason && state !== "dismissed" && (
        <p className="font-sans text-[13px] text-muted">
          <span className="text-ink">Why keep it: </span>
          {suggestion.reason}
        </p>
      )}
      {state !== "dismissed" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-sans text-[12.5px] text-muted">
          <span className="dl-label">Evidence</span>
          {suggestion.evidence.map((e) => (
            <span key={e.query_id} className="inline-flex items-center gap-1">
              {openQuery ? (
                <button
                  type="button"
                  onClick={() => openQuery(e.query_id)}
                  title={`Query ${e.query_id}: show it in the Queries tab`}
                  className="inline-flex items-center gap-1 rounded-[3px] border border-line px-1.5 py-px text-ink hover:border-ink"
                >
                  <Icon name="db" size={12} /> {e.tables.map((t) => t.replace(/^IHS_\d{4}\./, "")).join(", ") || e.query_id}
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
      )}

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
      {state === "accepted" && editId && (
        <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-sans text-[13px]">
          {shared ? (
            <span className="text-data">
              Shared to GitHub
              {shared.commit && (
                <>
                  {" · commit "}
                  {commit ? (
                    <a href={commit} target="_blank" rel="noopener noreferrer" className="font-mono underline underline-offset-4">
                      {shared.commit.slice(0, 7)}
                    </a>
                  ) : (
                    <span className="font-mono">{shared.commit.slice(0, 7)}</span>
                  )}
                </>
              )}
            </span>
          ) : (
            <span className="text-muted">A draft of {suggestion.page}, on this computer: not shared yet.</span>
          )}
          {!shared && (
            <Link to={editLink(suggestion.page, editId)} className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
              Review and Save & share in Knowledge
            </Link>
          )}
        </p>
      )}
      {(accept.error || dismiss.error) && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          {(accept.error ?? dismiss.error)?.message}
        </p>
      )}
    </section>
  );
}

/**
 * "Propose a Knowledge update": the person turns what this conversation found
 * into a proposal for the knowledge base themselves, with no AI request. A
 * small form (the page, the text, why, and the queries here that show it),
 * checked as the agent's suggestions are; it becomes a draft edit to review
 * and Save & share in the Knowledge tab.
 */
export function ProposeUpdateButton({ conversation }: { conversation: Conversation }) {
  const [open, setOpen] = useState(false);
  const status = useQuery({ queryKey: ["knowledge-status"], queryFn: knowledgeApi.status });
  const available = status.data?.available ?? false;
  return (
    <>
      <Button
        variant="ghost"
        className="px-2 text-[13px]"
        onClick={() => setOpen(true)}
        disabled={!available}
        title={available ? "Turn what you found here into a proposal for the lab knowledge base" : `${REAL_ONLY}: practice DataLab has no lab knowledge base`}
      >
        <Icon name="book" size={13} /> Propose a Knowledge update
      </Button>
      {open && <ProposeUpdateForm conversation={conversation} onClose={() => setOpen(false)} />}
    </>
  );
}

function ProposeUpdateForm({ conversation, onClose }: { conversation: Conversation; onClose: () => void }) {
  const queryClient = useQueryClient();
  const listId = useId();
  const pages = useQuery({ queryKey: ["kb-pages"], queryFn: knowledgeApi.pages });
  const queries = useQuery({ queryKey: ["data-accessed", conversation.id], queryFn: () => api.dataAccessed(conversation.id) });
  const usable = (queries.data ?? []).filter((q) => q.status === "succeeded");
  const [page, setPage] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState<Set<string>>(new Set());
  const propose = useMutation({
    mutationFn: () =>
      knowledgeApi.proposeUpdate(conversation.id, { page, title, text, reason, evidence_query_ids: [...evidence] }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["kb-edits"] });
      onClose();
    },
  });
  const ready = page.trim() && title.trim() && text.trim() && reason.trim() && evidence.size > 0;
  const field = "rounded-[3px] border border-line bg-field px-2.5 py-1.5 font-sans text-[13.5px] outline-none focus:border-ink";
  return (
    <Modal title="Propose a Knowledge update" onClose={onClose}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) propose.mutate();
        }}
      >
        <p className="font-sans text-[13px] text-muted">
          For something durable this conversation showed about the data: a quirk, what a column holds, a definition, a
          caveat. Not a one-off result, and never participant-level data or small counts.
        </p>
        <label className="flex flex-col gap-1">
          <span className="dl-label">Page</span>
          <input
            autoFocus
            list={listId}
            value={page}
            onChange={(e) => setPage(e.target.value)}
            placeholder="sources/fitbit.md, or a new page such as qc/zero-step-days.md"
            className={clsx(field, "font-mono text-[13px]")}
          />
          <datalist id={listId}>
            {(pages.data?.pages ?? [])
              .filter((p) => p.place === "page")
              .map((p) => (
                <option key={p.path} value={p.path} />
              ))}
          </datalist>
        </label>
        <label className="flex flex-col gap-1">
          <span className="dl-label">Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} className={field} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="dl-label">What to add (Markdown)</span>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} maxLength={4000} className={clsx(field, "resize-y")} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="dl-label">Why it's worth keeping</span>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={600} className={clsx(field, "resize-y")} />
        </label>
        <fieldset className="flex flex-col gap-1.5">
          <legend className="dl-label mb-1">Evidence: the queries here that show it</legend>
          {queries.isPending ? (
            <p className="font-sans text-[13px] text-muted">Loading this conversation's queries…</p>
          ) : usable.length === 0 ? (
            <p className="font-sans text-[13px] text-attn">This conversation has no queries that ran, so there's nothing to cite yet.</p>
          ) : (
            usable.map((q) => (
              <label key={q.id} className="flex cursor-pointer items-baseline gap-2 font-sans text-[13px]">
                <input
                  type="checkbox"
                  checked={evidence.has(q.id)}
                  onChange={(e) => {
                    const next = new Set(evidence);
                    if (e.target.checked) next.add(q.id);
                    else next.delete(q.id);
                    setEvidence(next);
                  }}
                  className="translate-y-[2px]"
                />
                <span>
                  <span className="text-ink">{q.tables.join(", ") || "A query"}</span>{" "}
                  <span className="text-faint">
                    {new Date(q.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {q.id}
                  </span>
                </span>
              </label>
            ))
          )}
        </fieldset>
        {propose.error && (
          <p role="alert" className="font-sans text-[13px] text-danger">
            {propose.error.message}
          </p>
        )}
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!ready || propose.isPending}>
              {propose.isPending ? "Checking…" : "Create the proposal"}
            </Button>
          </div>
          <p className="font-sans text-[12px] text-faint">
            It's checked for participant data, then kept as a draft on this computer. You review it and Save & share it in
            the Knowledge tab.
          </p>
        </div>
      </form>
    </Modal>
  );
}
