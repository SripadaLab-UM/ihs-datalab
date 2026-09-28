import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import { ApiError } from "@/api/http";
import { pipelinesApi } from "@/api/pipelines";
import {
  type DestinationChoice,
  type StageEdits,
  type StagesResult,
  type WorkflowSave,
  type WorkflowsStatus,
  workflowsApi,
} from "@/api/workflows";
import { SendingLine } from "@/components/chat/Pending";
import { CodeEditor, type EditorDiagnostic } from "@/components/editor/CodeEditor";
import { Button, Icon } from "@/components/ui";
import { ACTIONABLE } from "@/features/pipelines/pipelines";
import { useTabState } from "@/features/sql/hooks";

import { useRun } from "./hooks";
import { RunProgress } from "./RunView";
import { dedupe, Findings, problemsOf, Where } from "./SaveAsWorkflow";
import { StageCards } from "./StageCards";
import { byLine, problemWhere, runPath, workflowPath } from "./words";
import { PageHeader } from "./PageHeader";

export const EXAMPLE =
  "Extract Fitbit daily data for a date range, remove body-composition fields, check for duplicate " +
  "participant-days, and save the cleaned output to my local Dropbox folder.";

/** The three stages every workflow has, said once wherever a workflow is started. */
export function StagesExplainer() {
  return (
    <ol aria-label="How a workflow is built" className="grid gap-3 font-sans text-[13px] sm:grid-cols-3">
      {[
        ["Extract", "Pull the data you need from the database, for a date range or cohort you choose each run."],
        ["Process & QC", "Clean it (drop fields, reshape), then check it: no duplicates, nothing missing, small counts hidden."],
        ["Deliver", "Save the checked files to one of your export folders, only once every check has passed."],
      ].map(([title, text], i) => (
        <li key={title} className="flex gap-2.5">
          <span aria-hidden className="grid size-6 shrink-0 place-items-center rounded-full bg-ink text-[11.5px] font-semibold text-surface">
            {i + 1}
          </span>
          <span>
            <span className="block font-medium text-ink">{title}</span>
            <span className="text-muted">{text}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

/**
 * New workflow, in the middle of the page: the person says what it should do, the Workflow authoring
 * chat asks about the details and drafts it, and the draft is reviewed here as three editable stages,
 * test-run on practice data, and saved. Only the person saves: the agent never does.
 */
export function NewWorkflow({
  status,
  chatId,
  onStart,
  onShowChat,
  onNewChat,
}: {
  status: WorkflowsStatus | undefined;
  /** The Workflow authoring chat whose drafts this shows. */
  chatId: string;
  /** Start the chat with the description; resolves once it's sent. */
  onStart: (description: string) => Promise<void>;
  onShowChat: () => void;
  /** Forget the chat, to start another workflow from its description. */
  onNewChat: () => void;
}) {
  const practice = status?.profile === "practice";
  const [text, setText] = useTabState("datalab:workflows:draft", "");
  const [loadedFrom, setLoadedFrom] = useTabState("datalab:workflows:draft-from", "");
  const [pasting, setPasting] = useState(false);
  const found = useAgentDraft(chatId, status?.target?.kind === "share" || status?.target?.kind === "unavailable");
  const newer = found && found.from !== loadedFrom ? found : null;

  // The assistant's first draft opens by itself; a later one waits to be asked for, over edits.
  useEffect(() => {
    if (newer && !text) {
      setText(newer.text);
      setLoadedFrom(newer.from);
    }
  }, [newer, text, setText, setLoadedFrom]);

  const discard = (name?: string) => {
    if (text && !window.confirm("Discard this draft? Nothing has been saved.")) return;
    // Its test runs go too (their folders and records).
    if (name && practice) void workflowsApi.forgetTests(name).catch(() => undefined);
    setText("");
    setLoadedFrom(found?.from ?? "");
    setPasting(false);
  };
  const startAnother = () => {
    setText("");
    setLoadedFrom("");
    setPasting(false);
    onNewChat();
  };

  return (
    <div className="flex flex-col gap-6 px-6 pt-4 pb-10">
      <PageHeader>
        <div className="flex flex-col gap-1">
          <p className="font-sans text-[12.5px] text-muted">
            <Link to="/workflows" className="hover:text-ink">
              Workflows
            </Link>
          </p>
          <h1 className="font-serif text-[23px] leading-tight">New workflow</h1>
        </div>
      </PageHeader>

      {text || pasting ? (
        <>
          {newer && text && (
            <p role="status" className="flex flex-wrap items-center gap-2 rounded-[3px] bg-accent-soft px-3 py-2 font-sans text-[13px]">
              <span className="min-w-0 flex-1">The assistant has a newer draft ({newer.label}).</span>
              <Button
                className="px-2 py-0.5 text-[12.5px]"
                onClick={() => {
                  if (!window.confirm("Replace this draft, and your edits to it, with the assistant's newer one?")) return;
                  setText(newer.text);
                  setLoadedFrom(newer.from);
                }}
              >
                Use it
              </Button>
            </p>
          )}
          <DraftReview
            key={loadedFrom || "pasted"}
            text={text}
            onText={setText}
            practice={practice}
            conversationId={chatId}
            startInYaml={pasting && !text}
            onDiscard={discard}
            onStartAnother={startAnother}
          />
        </>
      ) : chatId ? (
        <Waiting onShowChat={onShowChat} onPaste={() => setPasting(true)} />
      ) : (
        <Describe practice={practice} onStart={onStart} onPaste={() => setPasting(true)} />
      )}
    </div>
  );
}

function Describe({
  practice,
  onStart,
  onPaste,
}: {
  practice: boolean;
  onStart: (description: string) => Promise<void>;
  onPaste: () => void;
}) {
  const [description, setDescription] = useState("");
  // Set at once on submit, before any state update can render: a double click starts one draft.
  const inFlight = useRef(false);
  const start = useMutation({
    mutationFn: () => onStart(description.trim()),
    onSettled: () => {
      inFlight.current = false;
    },
  });
  return (
    <form
      className="flex max-w-[52rem] flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (!description.trim() || inFlight.current) return;
        inFlight.current = true;
        start.mutate();
      }}
    >
      <label className="flex flex-col gap-3">
        <span id="new-workflow-question" className="font-serif text-[30px] leading-tight">
          What should this workflow do?
        </span>
        <span className="font-sans text-[13.5px] text-muted">
          Describe the task in your own words. The assistant asks about anything it needs (dates, cohort, fields,
          where the files go), then drafts it for you to review.
        </span>
        <textarea
          autoFocus
          aria-labelledby="new-workflow-question"
          rows={4}
          value={description}
          maxLength={4000}
          onChange={(e) => setDescription(e.target.value)}
          // Held still while it's sent; a failed start leaves it here to try again.
          readOnly={start.isPending}
          placeholder={EXAMPLE}
          className="w-full resize-y rounded-[4px] border border-line bg-field px-3 py-2.5 font-serif text-[17px] leading-relaxed outline-none focus:border-ink"
        />
      </label>
      {start.error && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          {start.error.message}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={!description.trim() || start.isPending}>
          <Icon name="spark" size={13} /> {start.isPending ? "Starting…" : "Start drafting"}
        </Button>
        <Button type="button" variant="ghost" onClick={onPaste}>
          Or write the YAML yourself
        </Button>
      </div>
      {start.isPending && <SendingLine />}
      <StagesExplainer />
      {practice && (
        <p className="font-sans text-[12.5px] text-muted">
          Practice DataLab: it runs on synthetic data, and what you save stays in a practice folder on this computer.
        </p>
      )}
    </form>
  );
}

function Waiting({ onShowChat, onPaste }: { onShowChat: () => void; onPaste: () => void }) {
  return (
    <div className="flex max-w-[52rem] flex-col gap-4">
      <p role="status" className="flex items-center gap-2.5 font-serif text-[20px]">
        <span aria-hidden className="dl-breathe size-2 rounded-full bg-ink" /> The assistant is working out the details.
      </p>
      <p className="font-sans text-[13.5px] text-muted">
        Answer its questions in the chat. When it has a draft, it appears here as three stages for you to review and edit.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button onClick={onShowChat}>
          <Icon name="spark" size={13} /> Show the chat
        </Button>
        <Button variant="ghost" onClick={onPaste}>
          Write the YAML yourself instead
        </Button>
      </div>
      <StagesExplainer />
    </div>
  );
}

// ------------------------------------------------------------------ the agent's draft

export type FoundDraft = { text: string; from: string; label: string };

/** The newest workflow file the Workflow authoring chat drafted: in its outputs (Practice, or no pipelines
 *  repo here), or in its Pipelines proposal. Looked for again after each turn. */
export function useAgentDraft(conversationId: string, proposals: boolean): FoundDraft | null {
  const outputs = useQuery({
    queryKey: ["workflow-draft-outputs", conversationId],
    queryFn: () => api.files(conversationId, "outputs"),
    enabled: Boolean(conversationId),
    refetchInterval: 5000,
    // The person may be reading the chat in another window meanwhile.
    refetchIntervalInBackground: true,
    retry: false,
  });
  const newest = useMemo(
    () =>
      (outputs.data ?? [])
        .filter((f) => /\.ya?ml$/i.test(f.path) && !f.path.includes("/"))
        .sort((a, b) => b.modified.localeCompare(a.modified))[0],
    [outputs.data],
  );
  const outputText = useQuery({
    queryKey: ["workflow-draft-output", conversationId, newest?.path, newest?.checkpoint],
    queryFn: () => api.fileText(conversationId, "outputs", newest!.path, newest!.checkpoint),
    enabled: Boolean(newest),
    retry: false,
  });
  const listed = useQuery({
    queryKey: ["pipeline-proposals", conversationId],
    queryFn: () => pipelinesApi.proposals(conversationId),
    enabled: Boolean(conversationId) && proposals,
    refetchInterval: 10_000,
    retry: false,
  });
  const open = listed.data?.find((p) => p.conversation_id === conversationId && ACTIONABLE.has(p.status));
  const proposal = useQuery({
    queryKey: ["pipeline-proposal", open?.id, open?.updated_at],
    queryFn: () => pipelinesApi.proposal(open!.id),
    enabled: Boolean(open),
    retry: false,
  });
  const inProposal = proposal.data?.files.find((f) => /^workflows\/[^/]+\.ya?ml$/i.test(f.path) && f.after);
  if (inProposal && open) {
    return { text: inProposal.after!, from: `proposal:${open.id}:${open.updated_at}`, label: inProposal.path };
  }
  if (newest && outputText.data && !outputText.data.truncated) {
    return { text: outputText.data.text, from: `outputs:${newest.path}@${newest.checkpoint}`, label: newest.path };
  }
  return null;
}

// ------------------------------------------------------------------ review

function DraftReview({
  text,
  onText,
  practice,
  conversationId,
  startInYaml,
  onDiscard,
  onStartAnother,
}: {
  text: string;
  onText: (text: string) => void;
  practice: boolean;
  conversationId: string;
  startInYaml: boolean;
  onDiscard: (name?: string) => void;
  onStartAnother: () => void;
}) {
  const [yaml, setYaml] = useState(startInYaml);
  const [checked, setChecked] = useState<StagesResult | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [saved, setSaved] = useState<WorkflowSave | null>(null);
  const [chosen, setChosen] = useState<DestinationChoice | null>(null);

  // The text as the backend last checked it; checked again after each pause in typing (the YAML view).
  useEffect(() => {
    if (checked?.text === text || !text.trim()) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      workflowsApi
        .stages({ text })
        .then((result) => !cancelled && setChecked(result))
        .catch((error: Error) => !cancelled && setEditError(error.message));
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [text, checked?.text]);

  const edit = useMutation({
    mutationFn: ({ edits }: { edits: StageEdits }) => workflowsApi.stages({ text, edits }),
    onMutate: () => setEditError(null),
    onSuccess: (result) => {
      setChecked(result);
      onText(result.text);
    },
    onError: (error) => {
      const problems = error instanceof ApiError ? problemsOf(error) : [];
      setEditError(problems.length ? problems.map((p) => `${p.path}: ${p.message}`).join(" ") : error.message);
    },
  });

  const current = checked?.text === text ? checked : null;
  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      (current?.problems ?? []).map((p) => ({
        line: p.line ?? 1,
        column: p.column ?? undefined,
        message: p.path && !p.line ? `${p.path}: ${p.message}` : p.message,
        severity: "error",
      })),
    [current?.problems],
  );
  const busy = edit.isPending || Boolean(saved && saved.state === "saving");

  if (saved?.state === "saved" || saved?.state === "already_there") {
    return <SavedNote saved={saved} practice={practice} onNew={onStartAnother} />;
  }

  return (
    <div className="flex flex-col gap-5">
      {current?.stages ? (
        <div className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-[16rem_minmax(0,1fr)]">
            <NameField value={current.stages.name} disabled={busy} onCommit={(name) => edit.mutate({ edits: { name } })} />
            <DescriptionField
              value={current.stages.description}
              disabled={busy}
              onCommit={(description) => edit.mutate({ edits: { description } })}
            />
          </div>
          <StageCards
            stages={current.stages}
            destinations={current.destinations}
            practice={practice}
            busy={busy}
            onEdit={(edits, picked) => {
              // A folder without a key here yet is mapped only when the workflow is saved.
              if (picked) setChosen(picked.mapped ? null : picked);
              edit.mutate({ edits });
            }}
          />
        </div>
      ) : (
        current && (
          <p className="font-sans text-[13px] text-attn">
            DataLab can't show this draft as stages until the YAML reads as a workflow. Fix it in the YAML view below.
          </p>
        )
      )}
      {!current && text.trim() && <p className="font-sans text-[13px] text-muted">Checking the draft…</p>}
      {editError && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          {editError}
        </p>
      )}

      {current && current.problems.length > 0 && (
        <section aria-label="Problems" className="flex flex-col">
          <p className="dl-label text-danger">Fix these before a test run or saving</p>
          <ul className="flex flex-col border-t border-line">
            {byLine(current.problems).map((p, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-3 border-b border-line py-1.5 font-sans text-[13px]">
                <span className="font-mono text-[12px] text-danger">{problemWhere(p)}</span>
                <span>{p.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {current?.valid && (
        <p className="flex items-center gap-1.5 font-sans text-[13px] text-data">
          <Icon name="check" size={13} /> Passes DataLab's workflow check.
        </p>
      )}

      <section className="flex flex-col gap-2">
        <Button
          variant="ghost"
          aria-expanded={yaml}
          className="self-start px-0 text-[13px]"
          onClick={() => setYaml(!yaml)}
        >
          <Icon name="code" size={13} /> Advanced: YAML {yaml ? "(hide)" : ""}
        </Button>
        {yaml && (
          <>
            <p className="font-sans text-[12.5px] text-muted">
              The workflow file itself. Edits here are checked the same way; the stages above follow them.
            </p>
            <CodeEditor
              label="The workflow file (YAML)"
              language="yaml"
              value={text}
              onChange={onText}
              readOnly={busy}
              diagnostics={current ? diagnostics : undefined}
              className="min-h-[16rem]"
            />
          </>
        )}
      </section>

      <TestRun text={text} practice={practice} ready={Boolean(current?.valid)} />

      <SaveDraft
        text={text}
        current={current}
        conversationId={conversationId}
        onSaved={setSaved}
        saved={saved}
        mapTo={chosen && current?.stages?.deliver?.destination === chosen.key ? chosen : null}
        onDiscard={() => onDiscard(current?.stages?.name)}
      />
    </div>
  );
}

function NameField({ value, disabled, onCommit }: { value: string; disabled: boolean; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }
  return (
    <label className="flex flex-col gap-1">
      <span className="dl-label">Name</span>
      <input
        aria-label="Name"
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value.toLowerCase().replace(/[\s.]+/g, "_"))}
        onBlur={() => draft !== value && onCommit(draft)}
        className="rounded-[3px] border border-line bg-field px-2.5 py-1.5 font-mono text-[13px] outline-none focus:border-ink"
      />
    </label>
  );
}

function DescriptionField({ value, disabled, onCommit }: { value: string; disabled: boolean; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }
  return (
    <label className="flex flex-col gap-1">
      <span className="dl-label">What it does</span>
      <input
        aria-label="Description"
        value={draft}
        disabled={disabled}
        maxLength={500}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        className="rounded-[3px] border border-line bg-field px-2.5 py-1.5 font-sans text-[13px] outline-none focus:border-ink"
      />
    </label>
  );
}

/** A test run of the draft on practice data: unsaved, never delivered. */
function TestRun({ text, practice, ready }: { text: string; practice: boolean; ready: boolean }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [ranText, setRanText] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: () => workflowsApi.testRun(text),
    onSuccess: (run) => {
      setRunId(run.id);
      setRanText(text);
    },
  });
  const run = useRun(runId ?? undefined);
  const problems = start.error instanceof ApiError ? problemsOf(start.error) : [];
  return (
    <section aria-labelledby="test-title" className="flex flex-col gap-2 border-t border-line pt-4">
      <h2 id="test-title" className="dl-label">
        Test run
      </h2>
      {practice ? (
        <>
          <p className="font-sans text-[13px] text-muted">
            Runs this draft on the synthetic database, exactly as a saved workflow would run, and shows every step and
            check. It isn't saved, and nothing is delivered.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!ready || start.isPending || Boolean(run.data && run.data.finished_at == null)} onClick={() => start.mutate()}>
              {start.isPending ? "Starting…" : runId ? "Test run again" : "Test run on practice data"}
            </Button>
            {runId && (
              <Link to={runPath(runId)} className="font-sans text-[13px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
                Open the test run
              </Link>
            )}
            {ranText !== null && ranText !== text && (
              <span className="font-sans text-[12.5px] text-attn">The draft changed since this test run.</span>
            )}
          </div>
        </>
      ) : (
        <p className="font-sans text-[13px] text-muted">
          Test runs of a draft happen on synthetic data, in Practice DataLab. Here, save it and check its first run
          before relying on it.
        </p>
      )}
      {start.error && (
        <div role="alert" className="font-sans text-[13px] text-danger">
          <p>{start.error.message}</p>
          {problems.map((p, i) => (
            <p key={i}>
              <span className="font-mono text-[12px]">{p.path}</span> {p.message}
            </p>
          ))}
        </div>
      )}
      {run.data && (
        <div className="flex flex-col gap-1">
          <RunProgress run={run.data} />
          {run.data.delivery_message && <p className="font-sans text-[12.5px] text-muted">{run.data.delivery_message}</p>}
        </div>
      )}
    </section>
  );
}

function SaveDraft({
  text,
  current,
  conversationId,
  saved,
  onSaved,
  mapTo,
  onDiscard,
}: {
  text: string;
  current: StagesResult | null;
  conversationId: string;
  saved: WorkflowSave | null;
  onSaved: (save: WorkflowSave) => void;
  /** The export folder chosen for the file's key, to map once it's saved. */
  mapTo: DestinationChoice | null;
  onDiscard: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const savedText = useRef<string | null>(null);
  const save = useMutation({
    // `usedBy`: the other workflow files the person confirmed name the key, when Save said so.
    mutationFn: (usedBy?: string[]) =>
      workflowsApi.save({
        text,
        confirmed: [...confirmed],
        source: "authoring",
        conversation_id: conversationId || null,
        map_destination: mapTo?.destination_id ?? null,
        confirm_key_used_by: usedBy ?? mapTo?.used_by ?? [],
      }),
    onMutate: () => {
      savedText.current = text;
    },
    onSuccess: (result) => {
      onSaved(result);
      if (result.state === "saved") void queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
  });
  // A Save & share runs the package's tests: followed until it's done.
  const followed = useQuery({
    queryKey: ["workflow-save", saved?.id],
    queryFn: () => workflowsApi.saveStatus(saved!.id!),
    enabled: Boolean(saved?.id) && saved?.state === "saving",
    refetchInterval: 1000,
  });
  useEffect(() => {
    const next = followed.data;
    if (!next || next.state === "saving") return;
    onSaved(next);
    if (next.state === "saved") void queryClient.invalidateQueries({ queryKey: ["workflows"] });
  }, [followed.data, onSaved, queryClient]);

  if (!current) return null;
  const target = current.target;
  const findings = dedupe(saved?.state === "check_failed" && savedText.current === text ? saved.findings : current.findings);
  const errors = findings.filter((f) => f.severity === "error");
  const unconfirmed = findings.filter((f) => f.severity !== "error" && !confirmed.has(f.id));
  const saving = save.isPending || saved?.state === "saving";
  const canSave = target.kind !== "unavailable" && current.valid && !saving && errors.length === 0 && unconfirmed.length === 0;
  const saveProblems = save.error instanceof ApiError ? problemsOf(save.error) : [];
  // Save refused: other workflow files name the key now (maybe more than the page knew).
  const usedBy = save.error instanceof ApiError ? usedByOf(save.error) : [];
  return (
    <section aria-labelledby="save-title" className="flex flex-col gap-3 border-t border-line pt-4 font-sans text-[13.5px]">
      <h2 id="save-title" className="dl-label">
        Save
      </h2>
      <Where target={target} />
      {findings.length > 0 && <Findings findings={findings} confirmed={confirmed} onConfirm={setConfirmed} />}
      {saved && saved.state !== "saving" && savedText.current === text && (
        <p role="alert" className="text-danger">
          {saved.message}
        </p>
      )}
      {save.error && (
        <div role="alert" className="text-danger">
          <p>{save.error.message}</p>
          {saveProblems.map((p, i) => (
            <p key={i}>
              <span className="font-mono text-[12px]">{p.path}</span> {p.message}
            </p>
          ))}
          {usedBy.length > 0 && mapTo && (
            <div className="mt-1.5 flex flex-col gap-1.5 text-ink">
              <p>
                They would deliver to {mapTo.name} too: <span className="font-mono text-[12px]">{usedBy.join(", ")}</span>
              </p>
              <Button className="self-start px-2.5 py-1 text-[12.5px]" disabled={saving} onClick={() => save.mutate(usedBy)}>
                Save, and deliver those to {mapTo.name} too
              </Button>
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={!canSave}
          onClick={() => {
            // A key other workflows use would send their files to this folder too: asked first.
            if (
              mapTo?.used_by.length &&
              !window.confirm(
                `${mapTo.used_by.join(", ")} also deliver to "${mapTo.key}". Save, and deliver those to ${mapTo.name} too?`,
              )
            )
              return;
            save.mutate(undefined);
          }}
        >
          {saving ? "Saving…" : target.kind === "share" ? "Save & share" : "Save"}
        </Button>
        <Button variant="ghost" disabled={saving} onClick={onDiscard}>
          Discard draft
        </Button>
        {saved?.state === "saving" && (
          <span role="status" className="text-muted">
            Checking, running the package's tests, and sharing. This takes a minute or two.
          </span>
        )}
      </div>
    </section>
  );
}

function SavedNote({ saved, practice, onNew }: { saved: WorkflowSave; practice: boolean; onNew: () => void }) {
  return (
    <div className="flex max-w-[46rem] flex-col gap-3 font-sans text-[13.5px]">
      <p className="flex items-baseline gap-1.5 text-data">
        <Icon name="check" size={13} className="translate-y-[2px]" />
        {saved.state === "already_there"
          ? "It was already there: someone saved this same file."
          : saved.shared
            ? "Saved and shared with the lab."
            : practice
              ? "Saved in Practice DataLab's workflows, on this computer."
              : "Saved on this computer."}
      </p>
      <p className="text-muted">{saved.message}</p>
      {saved.mapping && saved.mapping !== "none" && saved.mapping_message && (
        <p role={saved.mapping === "skipped" ? "alert" : undefined} className={saved.mapping === "skipped" ? "text-attn" : "text-muted"}>
          {saved.mapping_message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Link
          to={workflowPath(saved.path)}
          className="inline-flex items-center gap-1.5 rounded-[3px] bg-accent px-3 py-1.5 font-medium text-accent-ink hover:opacity-85"
        >
          <Icon name="open" size={13} /> Open it to run
        </Link>
        <Button onClick={onNew}>Start another</Button>
      </div>
    </div>
  );
}

/** The workflow files a refused Save says already name the key (`detail.used_by`). */
function usedByOf(error: ApiError): string[] {
  const detail = error.detail as { used_by?: unknown } | undefined;
  return Array.isArray(detail?.used_by) ? detail.used_by.filter((p): p is string => typeof p === "string") : [];
}
