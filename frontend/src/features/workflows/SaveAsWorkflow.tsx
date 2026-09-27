import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import type { QueryRecord } from "@/api/conversations";
import { ApiError } from "@/api/http";
import {
  type WorkflowDraft,
  type WorkflowDraftIn,
  type WorkflowFinding,
  type WorkflowProblem,
  type WorkflowSave,
  workflowsApi,
} from "@/api/workflows";
import { CodeEditor, type EditorDiagnostic } from "@/components/editor/CodeEditor";
import { Button, Icon, Modal } from "@/components/ui";

import { byLine, problemWhere, workflowPath } from "./words";

/** Where the draft's SQL comes from: the Playground's editor, or queries in a conversation's Data accessed log. */
export type DraftSource =
  | { kind: "playground"; sql: string; binds: Record<string, string | number | null> }
  | { kind: "conversation"; conversationId: string };

/** Save as workflow (SQL Playground) and Turn this into a workflow (a conversation): DataLab drafts the
 *  file from the SQL, the person names it, reviews and may edit the YAML, and saves it. Nothing is
 *  saved until they press Save. */
export function SaveAsWorkflow({
  source,
  suggestedName = "",
  onClose,
}: {
  source: DraftSource;
  suggestedName?: string;
  onClose: () => void;
}) {
  const [details, setDetails] = useState({ name: slug(suggestedName), description: "", destination: "" });
  const [draft, setDraft] = useState<WorkflowDraft | null>(null);
  // For a conversation: which of its queries go in (all that ran, until the person unticks some).
  const [picked, setPicked] = useState<string[] | null>(null);
  const make = useMutation({
    mutationFn: () => {
      const body: WorkflowDraftIn = {
        name: details.name,
        description: details.description,
        destination: details.destination.trim() || null,
      };
      if (source.kind === "playground") body.queries = [{ sql: source.sql, binds: source.binds }];
      else Object.assign(body, { conversation_id: source.conversationId, query_ids: picked ?? [] });
      return workflowsApi.draft(body);
    },
    onSuccess: setDraft,
  });
  const title = source.kind === "playground" ? "Save as workflow" : "Turn this into a workflow";
  // Escape, the backdrop and × never throw away a review being saved, nor edits unasked.
  const review = useRef({ busy: false, edited: false });
  const dismiss = () => {
    if (draft && review.current.busy) return;
    if (draft && review.current.edited && !window.confirm("Discard your edits to this workflow?")) return;
    onClose();
  };

  return (
    <Modal title={title} onClose={dismiss} wide={draft !== null}>
      {draft ? (
        <Review
          draft={draft}
          source={source}
          onBack={() => setDraft(null)}
          onClose={onClose}
          onState={(state) => {
            review.current = state;
          }}
        />
      ) : (
        <Details
          source={source}
          picked={picked}
          onPick={setPicked}
          details={details}
          onChange={setDetails}
          drafting={make.isPending}
          error={make.error?.message ?? null}
          onDraft={() => make.mutate()}
          onCancel={onClose}
        />
      )}
    </Modal>
  );
}

type DetailsValue = { name: string; description: string; destination: string };

function Details({
  source,
  picked,
  onPick,
  details,
  onChange,
  drafting,
  error,
  onDraft,
  onCancel,
}: {
  source: DraftSource;
  picked: string[] | null;
  onPick: (ids: string[]) => void;
  details: DetailsValue;
  onChange: (value: DetailsValue) => void;
  drafting: boolean;
  error: string | null;
  onDraft: () => void;
  onCancel: () => void;
}) {
  const keys = useQuery({ queryKey: ["workflow-destinations"], queryFn: workflowsApi.destinations });
  const listId = useId();
  const conversationId = source.kind === "conversation" ? source.conversationId : "";
  const logged = useQuery({
    queryKey: ["data-accessed", conversationId],
    queryFn: () => api.dataAccessed(conversationId),
    enabled: source.kind === "conversation",
  });
  const choices = useMemo(() => ranQueries(logged.data ?? []), [logged.data]);
  const chosen = picked ?? choices.map((q) => q.id);
  const count = source.kind === "playground" ? 1 : chosen.length;
  const ready = Boolean(details.name) && count > 0;
  return (
    <form
      className="flex flex-col gap-4 font-sans text-[13.5px]"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        if (source.kind === "conversation") onPick(chosen);
        onDraft();
      }}
    >
      <p className="text-muted">
        DataLab drafts a workflow from {count === 1 ? "this query" : `these ${count} queries`}: a SQL step each, the
        tables it reads, a parameter for each bind variable, and checks on the result, with a small-cells check where
        the query counts. You review the file before anything is saved. No AI is involved.
      </p>
      {source.kind === "conversation" && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="dl-label mb-1.5">The queries it ran (from Data accessed)</legend>
          {logged.isSuccess && choices.length === 0 && (
            <p className="text-muted">This conversation has no queries that ran, so there's nothing to draft from.</p>
          )}
          <ul className="flex max-h-56 flex-col gap-1.5 overflow-auto">
            {choices.map((q) => (
              <li key={q.id}>
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={chosen.includes(q.id)}
                    onChange={(e) =>
                      onPick(e.target.checked ? choices.map((c) => c.id).filter((id) => id === q.id || chosen.includes(id)) : chosen.filter((id) => id !== q.id))
                    }
                  />
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-[12px] text-ink">{q.tables.join(", ")}</span>
                    <code className="block truncate font-mono text-[11.5px] text-muted">{q.sql_text}</code>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      <label className="block">
        <span className="dl-label">Name</span>
        <input
          autoFocus
          required
          value={details.name}
          onChange={(e) => onChange({ ...details, name: slug(e.target.value) })}
          placeholder="fitbit_daily_2025"
          className="mt-1.5 w-full rounded-[3px] border border-line bg-field px-3 py-2 font-mono text-[13px] outline-none focus:border-ink"
        />
        <span className="mt-1 block text-[12px] text-muted">
          Lower case letters, digits, - and _. It names the file, its output and the delivery folder.
        </span>
      </label>
      <label className="block">
        <span className="dl-label">Description</span>
        <input
          value={details.description}
          maxLength={500}
          onChange={(e) => onChange({ ...details, description: e.target.value })}
          placeholder="What it extracts, and for whom"
          className="mt-1.5 w-full rounded-[3px] border border-line bg-field px-3 py-2 outline-none focus:border-ink"
        />
      </label>
      <label className="block">
        <span className="dl-label">Deliver to (a destination key)</span>
        <input
          list={listId}
          value={details.destination}
          onChange={(e) => onChange({ ...details, destination: e.target.value.toLowerCase() })}
          placeholder="None: keep the outputs in the run folder"
          className="mt-1.5 w-full rounded-[3px] border border-line bg-field px-3 py-2 font-mono text-[13px] outline-none focus:border-ink"
        />
        <datalist id={listId}>
          {keys.data?.map((k) => (
            <option key={k.key} value={k.key}>
              {k.name ?? "not set on this computer"}
            </option>
          ))}
        </datalist>
        <span className="mt-1 block text-[12px] text-muted">
          A key each computer maps to one of its export folders (Settings), so the file works for everyone.
        </span>
      </label>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={drafting || !ready}>
          {drafting ? "Drafting…" : "Draft the workflow"}
        </Button>
      </div>
    </form>
  );
}

function Review({
  draft,
  source,
  onBack,
  onClose,
  onState,
}: {
  draft: WorkflowDraft;
  source: DraftSource;
  onBack: () => void;
  onClose: () => void;
  onState: (state: { busy: boolean; edited: boolean }) => void;
}) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(draft.text);
  const checked = useChecked(text, draft);
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [job, setJob] = useState<WorkflowSave | null>(null);
  // The text the last save was of: its outcome is about that text only.
  const [jobText, setJobText] = useState<string | null>(null);
  const saving = job?.state === "saving";
  const target = draft.target;

  const save = useMutation({
    mutationFn: () =>
      workflowsApi.save({
        text,
        confirmed: [...confirmed],
        source: source.kind,
        conversation_id: source.kind === "conversation" ? source.conversationId : null,
      }),
    onMutate: () => setJobText(text),
    onSuccess: (result) => {
      setJob(result);
      if (result.state === "saved") void queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
  });
  // A Save & share runs the package's tests: follow it until it's done.
  const followed = useQuery({
    queryKey: ["workflow-save", job?.id],
    queryFn: () => workflowsApi.saveStatus(job!.id!),
    enabled: Boolean(job?.id) && saving,
    refetchInterval: 1000,
  });
  useEffect(() => {
    const next = followed.data;
    if (!next || next.state === "saving") return;
    setJob(next);
    if (next.state === "saved") void queryClient.invalidateQueries({ queryKey: ["workflows"] });
  }, [followed.data, queryClient]);

  const busy = saving || save.isPending;
  const edited = text !== draft.text;
  useEffect(() => onState({ busy, edited }), [busy, edited, onState]);

  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      checked.problems.map((p) => ({
        line: p.line ?? 1,
        column: p.column ?? undefined,
        message: p.path && !p.line ? `${p.path}: ${p.message}` : p.message,
        severity: "error",
      })),
    [checked.problems],
  );

  if (job?.state === "already_there") {
    return (
      <div className="flex flex-col gap-3 font-sans text-[13.5px]">
        <p className="text-attn">{job.message}</p>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Close</Button>
          <Link
            to={workflowPath(job.path)}
            onClick={onClose}
            className="inline-flex items-center gap-1.5 rounded-[3px] bg-accent px-3 py-1.5 font-sans text-[13.5px] font-medium text-accent-ink hover:opacity-85"
          >
            <Icon name="open" size={13} /> Open theirs in Workflows
          </Link>
        </div>
      </div>
    );
  }

  if (job?.state === "saved") {
    return (
      <div className="flex flex-col gap-3 font-sans text-[13.5px]">
        <p className="flex items-baseline gap-1.5 text-data">
          <Icon name="check" size={13} className="translate-y-[2px]" />
          {job.shared ? "Saved and shared with the lab." : "Saved."}
        </p>
        <p className="text-muted">
          {job.shared ? (
            <>
              It's <span className="font-mono text-ink">{job.path}</span> in the pipelines repo
              {job.commit && (
                <>
                  , commit <span className="font-mono">{job.commit.slice(0, 10)}</span>
                </>
              )}
              .
            </>
          ) : (
            job.message
          )}
        </p>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Close</Button>
          <Link
            to={workflowPath(job.path)}
            onClick={onClose}
            className="inline-flex items-center gap-1.5 rounded-[3px] bg-accent px-3 py-1.5 font-sans text-[13.5px] font-medium text-accent-ink hover:opacity-85"
          >
            <Icon name="open" size={13} /> Open it in Workflows
          </Link>
        </div>
      </div>
    );
  }

  // What the data check finds in the text as it is now: a finding edited away no longer blocks.
  // A save's own findings (after others' changes, say) count while the text is what was saved.
  const current = jobText === text && job?.state === "check_failed" ? job.findings : checked.text === text ? checked.findings : [];
  const findings = dedupe(current);
  const jobCurrent = job !== null && jobText === text;
  const blockingErrors = findings.filter((f) => f.severity === "error");
  const unconfirmed = findings.filter((f) => f.severity !== "error" && !confirmed.has(f.id));
  const saveProblems = save.error instanceof ApiError ? problemsOf(save.error) : [];
  const canSave =
    target.kind !== "unavailable" &&
    checked.valid &&
    !checked.checking &&
    !saving &&
    !save.isPending &&
    blockingErrors.length === 0 &&
    unconfirmed.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 font-sans text-[13.5px]">
      <Where target={target} />
      {draft.notes.length > 0 && (
        <ul className="flex flex-col gap-1 text-[13px] text-attn">
          {draft.notes.map((note) => (
            <li key={note} className="flex items-baseline gap-1.5">
              <Icon name="alert" size={12} className="shrink-0 translate-y-[1px]" />
              {note}
            </li>
          ))}
        </ul>
      )}
      <CodeEditor
        label="The drafted workflow file"
        language="yaml"
        value={text}
        onChange={setText}
        readOnly={busy}
        diagnostics={checked.text === text ? diagnostics : undefined}
        className="min-h-[16rem] flex-1"
      />
      {checked.problems.length > 0 && checked.text === text && (
        <section aria-label="Problems" className="flex flex-col">
          <p className="dl-label text-danger">Fix these before saving</p>
          <ul className="flex flex-col border-t border-line">
            {byLine(checked.problems).map((p, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-3 border-b border-line py-1.5 text-[13px]">
                <span className="font-mono text-[12px] text-danger">{problemWhere(p)}</span>
                <span>{p.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {findings.length > 0 && <Findings findings={findings} confirmed={confirmed} onConfirm={setConfirmed} />}
      {job && jobCurrent && job.state !== "saving" && (
        <p role="alert" className="text-danger">
          {job.message}
        </p>
      )}
      {save.error && (
        <div role="alert" className="text-danger">
          <p>{save.error.message}</p>
          {saveProblems.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {saveProblems.map((p, i) => (
                <li key={i}>
                  <span className="font-mono text-[12px]">{p.path}</span> {p.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {saving && (
          <span role="status" className="mr-auto flex items-center gap-2 text-muted">
            <span className="size-2 rounded-full bg-ink [animation:dl-breathe_1.6s_ease-in-out_infinite]" />
            Checking, running the package's tests, and sharing. This takes a minute or two.
          </span>
        )}
        <Button onClick={onBack} disabled={busy}>
          Back
        </Button>
        <Button variant="primary" disabled={!canSave} onClick={() => save.mutate()}>
          {save.isPending || saving
            ? "Saving…"
            : target.kind === "share"
              ? jobCurrent && job?.state === "check_failed"
                ? "Save & share again"
                : "Save & share"
              : "Save"}
        </Button>
      </div>
    </div>
  );
}

/** Where Save puts the file, said before it's pressed. */
function Where({ target }: { target: WorkflowDraft["target"] }) {
  if (target.kind === "share") {
    return (
      <p className="flex items-baseline gap-1.5 text-[13px] text-muted">
        <Icon name="shield" size={13} className="shrink-0 translate-y-[2px]" /> {target.message}
      </p>
    );
  }
  if (target.kind === "local") {
    return (
      <p className="flex items-baseline gap-1.5 text-[13px] text-attn">
        <Icon name="folder" size={13} className="shrink-0 translate-y-[2px]" /> {target.message}
      </p>
    );
  }
  return (
    <p role="alert" className="flex items-baseline gap-1.5 text-[13px] text-danger">
      <Icon name="alert" size={13} className="shrink-0 translate-y-[2px]" /> It can't be saved yet. {target.message}
    </p>
  );
}

/** The file as last checked: the draft's own verdict, then again after each pause in editing. */
function useChecked(text: string, draft: WorkflowDraft) {
  const [state, setState] = useState({
    text: draft.text,
    valid: draft.valid,
    problems: draft.problems as WorkflowProblem[],
    findings: draft.findings as WorkflowFinding[],
  });
  const checking = state.text !== text;
  useEffect(() => {
    if (!checking) return;
    const controller = { cancelled: false };
    const timer = window.setTimeout(() => {
      workflowsApi
        .checkDraft(text)
        .then((result) => {
          if (!controller.cancelled)
            setState({ text, valid: result.valid, problems: result.problems, findings: result.findings });
        })
        .catch((error: Error) => {
          if (!controller.cancelled)
            setState({
              text,
              valid: false,
              problems: [{ path: "", message: error.message, line: null, column: null }],
              findings: [],
            });
        });
    }, 400);
    return () => {
      controller.cancelled = true;
      window.clearTimeout(timer);
    };
  }, [text, checking]);
  return { ...state, checking };
}

function Findings({
  findings,
  confirmed,
  onConfirm,
}: {
  findings: WorkflowFinding[];
  confirmed: Set<string>;
  onConfirm: (next: Set<string>) => void;
}) {
  const errors = findings.filter((f) => f.severity === "error");
  const others = findings.filter((f) => f.severity !== "error");
  return (
    <section aria-label="The check's findings" className="flex flex-col gap-2 text-[13px]">
      {errors.map((f) => (
        <p key={f.id} className="text-danger">
          <span className="font-mono text-[12.5px]">{f.path}</span>: {f.message}
        </p>
      ))}
      {others.length > 0 && (
        <>
          <p className="text-attn">
            This may be participant data, and the file will be on GitHub. Look at each one, and confirm it isn't;
            otherwise change the file (a value that identifies someone belongs in a parameter, given when it runs).
          </p>
          <ul className="flex flex-col gap-2">
            {others.map((f) => (
              <li key={f.id}>
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={confirmed.has(f.id)}
                    onChange={(e) => {
                      const next = new Set(confirmed);
                      if (e.target.checked) next.add(f.id);
                      else next.delete(f.id);
                      onConfirm(next);
                    }}
                  />
                  <span className="min-w-0">
                    {f.line ? `Line ${f.line}: ` : ""}
                    {f.message} <span className="text-muted">I've checked: it isn't participant data.</span>
                    {f.text && (
                      <code className="mt-1 block overflow-x-auto rounded-[3px] bg-sunken px-1.5 py-0.5 font-mono text-[12px] whitespace-pre text-ink">
                        {f.text}
                      </code>
                    )}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function dedupe(findings: WorkflowFinding[]): WorkflowFinding[] {
  return [...new Map(findings.map((f) => [f.id, f])).values()];
}

function problemsOf(error: ApiError): WorkflowProblem[] {
  const detail = error.detail as { problems?: { path: string; message: string }[] } | undefined;
  return (detail?.problems ?? []).map((p) => ({ ...p, line: null, column: null }));
}

/** The queries that ran, oldest first, each SQL once (its latest run). */
function ranQueries(records: QueryRecord[]): QueryRecord[] {
  const latest = new Map<string, QueryRecord>();
  for (const q of records) if (q.status === "succeeded") latest.set(q.sql_text.trim(), q);
  return [...latest.values()].sort((a, b) => a.started_at.localeCompare(b.started_at));
}

/** A workflow name as the file check wants it: lower case, digits, - and _. */
export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\s.]+/g, "_")
    .replace(/[^a-z0-9_-]/g, "")
    .slice(0, 64);
}
