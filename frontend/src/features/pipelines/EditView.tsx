import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/http";
import { commitUrl } from "@/api/knowledge";
import { type PipelineEdit, type PipelineFinding, pipelinesApi } from "@/api/pipelines";
import { CodeEditor, type EditorDiagnostic } from "@/components/editor/CodeEditor";
import { DiffView } from "@/components/editor/DiffView";
import { Button, Chip, Icon, InfoTip, Tabs } from "@/components/ui";
import { settingsLink } from "@/features/settings/highlight";

import { EDIT_OPEN, editChip, languageOf, when } from "./pipelines";
import { Findings } from "./ProposalView";
import { TestResults } from "./TestResults";

/** How long the editor waits after typing before it checks the text again. */
export const CHECK_DELAY_MS = 500;

/** What to open: an edit, or a file (its open edit, or a new one), or a new file. */
export type EditTarget = { id: string } | { path: string; isNew?: boolean };

// What's typed but not yet kept, per edit, in this browser: a reload or a
// closed tab doesn't lose it. The kept draft is DataLab's (the database, on
// this computer), for every browser.
const UNSAVED = "datalab:pipelines:unsaved:";

export interface Unsaved {
  files: Record<string, string | null>;
  version: string;
}

export function readUnsaved(editId: string): Unsaved | null {
  try {
    const raw = JSON.parse(localStorage.getItem(UNSAVED + editId) ?? "null") as Partial<Unsaved> | null;
    return raw && typeof raw.version === "string" && raw.files && typeof raw.files === "object"
      ? { files: raw.files, version: raw.version }
      : null;
  } catch {
    return null;
  }
}

function writeUnsaved(editId: string, unsaved: Unsaved | null) {
  try {
    if (unsaved === null) localStorage.removeItem(UNSAVED + editId);
    else localStorage.setItem(UNSAVED + editId, JSON.stringify(unsaved));
  } catch {
    // Storage can be unavailable (a private window): the text lasts while the page is open.
  }
}

function useSettled<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

const keyOf = (target: EditTarget) =>
  ["pipeline-edit", "id" in target ? target.id : `${target.isNew ? "new" : "file"}:${target.path}`] as const;
const saving = (edit: PipelineEdit) => edit.status === "saving" || edit.test?.status === "running";

/**
 * A person's own change to the pipelines repo, with no AI request: the file
 * (or, from an assistant's proposal, each of its files) in the editor, the
 * proposal's check as they type, the package's tests, and Save & share, as
 * for an assistant's proposal. "Keep as a draft" keeps it on this computer
 * only; Save & share commits it as the person and pushes it to GitHub. If a
 * file changed on GitHub meanwhile, the three versions are shown first.
 */
export function EditView({
  target,
  repo,
  signedIn,
  onLoaded,
  onClose,
  onOpenProposal,
}: {
  target: EditTarget;
  repo: string | null | undefined;
  signedIn: boolean;
  /** Once it's open: its id, so a reload opens the same edit. */
  onLoaded?: (id: string) => void;
  onClose: () => void;
  onOpenProposal?: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const key = keyOf(target);
  const loaded = useQuery({
    queryKey: key,
    queryFn: () =>
      "id" in target ? pipelinesApi.getEdit(target.id) : pipelinesApi.startEdit(target.path, target.isNew ?? false),
    retry: false,
    staleTime: Infinity,
    // While its tests or its save run in the background.
    refetchInterval: (query) => (query.state.data && saving(query.state.data) ? 1500 : false),
  });
  const loadedId = loaded.data?.id;
  useEffect(() => {
    if (loadedId && !("id" in target)) {
      queryClient.setQueryData(["pipeline-edit", loadedId], loaded.data);
      onLoaded?.(loadedId);
    }
    // Only when it first opens.
  }, [loadedId]);
  if (loaded.error) {
    return (
      <div className="flex flex-col gap-3 px-5 py-6">
        <p role="alert" className="font-sans text-[13.5px] text-danger">
          {loaded.error.message}
        </p>
        <div>
          <Button onClick={onClose}>Back to the files</Button>
        </div>
      </div>
    );
  }
  if (!loaded.data) {
    return (
      <p className="px-5 py-6 font-sans text-[13px] text-muted">
        {"path" in target ? `Opening ${target.path} for editing…` : "Opening your edit…"}
      </p>
    );
  }
  return (
    <Editor
      key={loaded.data.id}
      edit={loaded.data}
      onEdit={(next) => {
        queryClient.setQueryData(key, next);
        queryClient.setQueryData(["pipeline-edit", next.id], next);
        void queryClient.invalidateQueries({ queryKey: ["pipeline-edits"] });
      }}
      repo={repo}
      signedIn={signedIn}
      onClose={onClose}
      onOpenProposal={onOpenProposal}
    />
  );
}

function Editor({
  edit,
  onEdit,
  repo,
  signedIn,
  onClose,
  onOpenProposal,
}: {
  edit: PipelineEdit;
  onEdit: (edit: PipelineEdit) => void;
  repo: string | null | undefined;
  signedIn: boolean;
  onClose: () => void;
  onOpenProposal?: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const open = EDIT_OPEN.has(edit.status);
  const kept = useMemo(() => Object.fromEntries(edit.files.map((f) => [f.path, f.text])), [edit.files]);
  // What's typed here, from this browser's unkept copy if there is one. Typed
  // over the draft as it still is, it comes back at once; over an older one
  // (kept since, in another window), only if the person asks.
  const [unkept] = useState(() => {
    const found = open ? readUnsaved(edit.id) : null;
    return found && !same(found.files, kept) ? found : null;
  });
  const restored = unkept && unkept.version === edit.updated_at ? unkept.files : null;
  const [olderUnkept, setOlderUnkept] = useState(unkept && restored === null ? unkept.files : null);
  const [texts, setTexts] = useState<Record<string, string | null>>(() =>
    restored ? onlyKnown(restored, kept) : kept,
  );
  const dirty = !same(texts, kept);
  useEffect(() => {
    if (open && olderUnkept === null)
      writeUnsaved(edit.id, dirty ? { files: texts, version: edit.updated_at } : null);
  }, [edit.id, edit.updated_at, texts, dirty, open, olderUnkept]);

  const paths = Object.keys(texts);
  const [active, setActive] = useState(paths[0] ?? "");
  const file = edit.files.find((f) => f.path === active) ?? edit.files[0];
  const text = texts[active] ?? null;
  const [view, setView] = useState<"edit" | "review">("edit");
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [discarding, setDiscarding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // The proposal's check, on what's in the editor, once the typing stops.
  const settled = useSettled(text, CHECK_DELAY_MS);
  const readOnly = Boolean(file?.source_note) || text === null;
  const check = useQuery({
    queryKey: ["pipeline-edit-check", edit.id, edit.updated_at, active, settled],
    queryFn: () => pipelinesApi.checkEdit(edit.id, active, settled ?? ""),
    placeholderData: (previous) => previous,
    enabled: open && settled !== null && !readOnly,
  });
  const checking = open && !readOnly && (settled !== text || check.isFetching);
  const findings: PipelineFinding[] = check.data && !readOnly ? check.data.findings : edit.findings;
  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      findings
        .filter((f) => f.line && f.path === active)
        .map((f) => ({ line: f.line!, message: f.message, severity: f.severity === "error" ? "error" : "warning" })),
    [findings, active],
  );

  const keepNow = async (): Promise<PipelineEdit> => {
    if (!dirty) return edit;
    const next = await pipelinesApi.keepEdit(edit.id, texts, edit.updated_at);
    writeUnsaved(edit.id, null);
    onEdit(next);
    return next;
  };
  const stale = (error: Error) => {
    if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: ["pipeline-edit"] });
  };
  const keep = useMutation({
    mutationFn: keepNow,
    onSuccess: () => setNotice("Kept on this computer (not shared)."),
    onError: stale,
  });
  const test = useMutation({
    mutationFn: async () => pipelinesApi.testEdit((await keepNow()).id),
    onSuccess: onEdit,
    onError: stale,
  });
  const share = useMutation({
    mutationFn: async () => {
      const next = await keepNow();
      return pipelinesApi.shareEdit(next.id, [...confirmed], next.updated_at);
    },
    onSuccess: (next) => {
      onEdit(next);
      setNotice(null);
    },
    onError: stale,
  });
  const discard = useMutation({
    mutationFn: () => pipelinesApi.discardEdit(edit.id),
    onSuccess: (next) => {
      writeUnsaved(edit.id, null);
      onEdit(next);
      onClose();
    },
  });
  useEffect(() => {
    if (edit.status === "saved") {
      writeUnsaved(edit.id, null);
      for (const k of ["pipelines-status", "pipelines-files", "pipelines-file"])
        void queryClient.invalidateQueries({ queryKey: [k] });
    }
  }, [edit.status, edit.id, queryClient]);
  const busy = keep.isPending || share.isPending || discard.isPending || test.isPending || saving(edit);

  if (edit.status === "saved") {
    const url = edit.commit ? commitUrl(repo, edit.commit) : null;
    return (
      <section aria-label="Your edit" className="flex flex-col gap-4 px-5 pt-4 pb-8">
        <Heading edit={edit} />
        <p role="status" className="flex flex-wrap items-baseline gap-2 font-sans text-[14px] text-data">
          <Icon name="check" size={14} className="translate-y-[2px]" /> Shared to GitHub
          {edit.commit && (
            <>
              {" · commit "}
              {url ? (
                <a href={url} target="_blank" rel="noopener noreferrer" className="font-mono underline underline-offset-4">
                  {edit.commit.slice(0, 7)}
                </a>
              ) : (
                <span className="font-mono">{edit.commit.slice(0, 7)}</span>
              )}
            </>
          )}
        </p>
        <p className="font-sans text-[13px] text-muted">
          The tests passed on it, and it's committed as you and pushed to the lab's pipelines repo. Conversations get it
          in their next copy.
        </p>
        <div>
          <Button variant="primary" onClick={onClose}>
            Back to the files
          </Button>
        </div>
      </section>
    );
  }
  if (edit.status === "discarded") {
    return (
      <section aria-label="Your edit" className="flex flex-col gap-3 px-5 pt-4 pb-8">
        <Heading edit={edit} />
        <p className="font-sans text-[13.5px] text-muted">This edit was discarded. Nothing was shared.</p>
        <div>
          <Button onClick={onClose}>Back to the files</Button>
        </div>
      </section>
    );
  }

  const errors = findings.filter((f) => f.severity === "error");
  const unconfirmed = findings.filter((f) => f.severity !== "error" && !confirmed.has(f.id));
  const unchanged = edit.files.every((f) => !f.new && texts[f.path] === f.before) && paths.length === edit.files.length;
  const blocker = edit.upstream_changed
    ? "A file changed on GitHub since you started: see the versions above first."
    : !signedIn
      ? "Sign in to GitHub to share."
      : unchanged
        ? "No changes yet."
        : checking
          ? "Checking…"
          : errors.length
            ? "Fix what the check found before sharing."
            : unconfirmed.length
              ? "Confirm each finding of the check (below) before sharing."
              : null;
  const result = edit.result;
  const leaveOut = (path: string) => {
    const next = { ...texts };
    delete next[path];
    setTexts(next);
    if (active === path) setActive(Object.keys(next)[0] ?? "");
  };

  return (
    <section aria-label="Your edit" data-edit-scroll className="min-h-0 flex-1 overflow-y-auto px-5 pt-4 pb-8">
      {/* Not the scrolling box itself: in it, a flex column would squash the tabs to nothing. */}
      <div className="flex flex-col gap-4">
        <Heading edit={edit} dirty={dirty} unchanged={unchanged} />
        <p className="-mt-2 font-sans text-[13px] text-muted">
          On <span className="font-mono text-[12px]">{edit.base.slice(0, 7)}</span> of main, started {when(edit.created_at)}.
          Checked and tested as the assistant's proposals are, and shared only when you choose Save &amp; share.
        </p>
        {edit.origin && (
          <p className="rounded-[3px] border border-line bg-sunken px-3 py-2 font-sans text-[12.5px] text-muted">
            <span className="text-ink">From a change suggested by the assistant</span> in “
            {edit.origin.conversation_title ?? edit.origin.conversation_id}”, which this edit replaces.{" "}
            {onOpenProposal && (
              <button
                type="button"
                onClick={() => onOpenProposal(edit.origin!.proposal_id)}
                className="text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
              >
                See what it suggested
              </button>
            )}
          </p>
        )}
        {restored !== null && (
          <p role="status" className="font-sans text-[12.5px] text-attn">
            Restored changes you hadn't kept yet, from this browser.
          </p>
        )}
        {olderUnkept !== null && (
          <p role="status" className="flex flex-wrap items-baseline gap-x-2 font-sans text-[12.5px] text-attn">
            This browser has changes you hadn't kept, typed over an older version of this draft (it was kept since, in
            another window). This is the newer draft.
            <Button
              variant="ghost"
              className="px-1 py-0 text-[12.5px] underline"
              onClick={() => {
                setTexts(onlyKnown(olderUnkept, kept));
                setOlderUnkept(null);
              }}
            >
              Put back my unkept changes
            </Button>
            <Button
              variant="ghost"
              className="px-1 py-0 text-[12.5px]"
              onClick={() => {
                writeUnsaved(edit.id, null);
                setOlderUnkept(null);
              }}
            >
              Forget them
            </Button>
          </p>
        )}
        {edit.upstream_changed && (
          <Conflict
            edit={edit}
            texts={texts}
            keepFirst={keepNow}
            onMoved={(next) => {
              onEdit(next);
              setTexts(Object.fromEntries(next.files.map((f) => [f.path, f.text])));
              writeUnsaved(next.id, null);
            }}
          />
        )}

        <Tabs
          tabs={[
            { id: "edit" as const, label: "Edit" },
            { id: "review" as const, label: "Review changes" },
          ]}
          value={view}
          onChange={setView}
        />

        {view === "edit" && file && (
          <div className="flex flex-col gap-2">
            {paths.length > 1 && (
              <div role="group" aria-label="Files in this edit" className="flex flex-wrap gap-1.5">
                {paths.map((p) => (
                  <button
                    key={p}
                    type="button"
                    aria-pressed={p === active}
                    onClick={() => setActive(p)}
                    className={clsx(
                      "rounded-[3px] px-2 py-0.5 font-mono text-[12px]",
                      p === active ? "bg-surface text-ink shadow-[inset_0_0_0_1px_var(--color-line)]" : "text-muted hover:text-ink",
                    )}
                  >
                    {p.split("/").pop()}
                    {texts[p] !== kept[p] ? " •" : ""}
                  </button>
                ))}
              </div>
            )}
            <p className="flex flex-wrap items-baseline gap-x-3 font-mono text-[12.5px]">
              <span className="text-ink">{active}</span>
              {file.new && <span className="font-sans text-muted">new file</span>}
              {paths.length > 1 && open && !busy && (
                <Button variant="ghost" className="px-1 py-0 text-[12px]" onClick={() => leaveOut(active)}>
                  Leave this file out
                </Button>
              )}
            </p>
            {file.source_note && (
              <p className="font-sans text-[12.5px] text-attn">
                Not edited by hand: {file.source_note} It's shared as it is here.
              </p>
            )}
            {text === null ? (
              <p className="font-sans text-[13px] text-muted">This change deletes {active}.</p>
            ) : (
              <CodeEditor
                label={`${active}: your edit`}
                language={languageOf(active)}
                value={text}
                onChange={(value) => setTexts({ ...texts, [active]: value })}
                readOnly={readOnly || !open || busy}
                diagnostics={diagnostics}
                className="h-[min(60vh,36rem)]"
              />
            )}
            <CheckLine findings={findings.filter((f) => f.path === active)} checking={checking} />
          </div>
        )}
        {view === "review" && (
          <div className="flex flex-col gap-5">
            <p className="font-sans text-[12.5px] text-muted">Against where you started (−), what Save &amp; share would commit (+).</p>
            {paths.map((p) => {
              const f = edit.files.find((x) => x.path === p)!;
              return (
                <div key={p}>
                  <p className="mb-1.5 flex items-center gap-2 font-mono text-[12.5px]">
                    <span className="text-ink">{p}</span>
                    <span className="text-muted">{f.new ? "added" : texts[p] === null ? "deleted" : "modified"}</span>
                  </p>
                  <DiffView
                    label={`${p}: your changes`}
                    original={f.before ?? ""}
                    modified={texts[p] ?? ""}
                    language={languageOf(p)}
                    layout="unified"
                    className="max-h-[32rem] overflow-auto"
                  />
                </div>
              );
            })}
          </div>
        )}

        <section aria-labelledby={`${edit.id}-tests`} className="border-t border-line pt-4">
          <h3 id={`${edit.id}-tests`} className="dl-label mb-2">
            Tests
          </h3>
          <TestResults test={edit.test} />
          {dirty && edit.test && edit.test.status !== "running" && (
            <p className="mt-1 font-sans text-[12.5px] text-muted">These ran on the kept draft, before your latest changes.</p>
          )}
          {open && edit.test?.status !== "running" && (
            <Button className="mt-3" onClick={() => test.mutate()} disabled={busy || unchanged}>
              <Icon name="check" size={13} /> {edit.test ? "Run the tests again" : "Run the tests"}
            </Button>
          )}
        </section>

        {findings.length > 0 && (
          <section aria-labelledby={`${edit.id}-check`} className="border-t border-line pt-4">
            <h3 id={`${edit.id}-check`} className="dl-label mb-2">
              Check
            </h3>
            <Findings findings={findings} confirmed={confirmed} onConfirm={setConfirmed} disabled={!open || busy} yours />
          </section>
        )}

        {edit.status === "saving" && (
          <p className="font-sans text-[13.5px] text-muted" aria-live="polite">
            <span className="dl-breathe mr-2 inline-block size-[6px] rounded-full bg-ink" />
            Saving: {edit.test?.status === "running" ? "the tests are running first." : "checking and sharing it."}
          </p>
        )}
        {result && edit.status !== "draft" && edit.status !== "saving" && (
          <p role="alert" className={clsx("font-sans text-[13px]", edit.status === "conflict" || edit.status === "check_failed" ? "text-attn" : "text-danger")}>
            {result.message}
          </p>
        )}
        {notice && !dirty && (
          <p role="status" className="font-sans text-[13px] text-muted">
            {notice}
          </p>
        )}
        {(keep.error || share.error || discard.error || test.error) && (
          <p role="alert" className="font-sans text-[13px] text-danger">
            {(keep.error ?? share.error ?? discard.error ?? test.error)?.message}
          </p>
        )}

        <div className="flex flex-col gap-2 border-t border-line pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" onClick={onClose} disabled={keep.isPending || share.isPending}>
              Close
            </Button>
            {discarding ? (
              <Button variant="danger" onClick={() => discard.mutate()} disabled={busy}>
                Discard this edit
              </Button>
            ) : (
              <Button variant="ghost" onClick={() => setDiscarding(true)} disabled={busy}>
                <Icon name="trash" size={13} /> Discard…
              </Button>
            )}
            <span className="flex-1" />
            <Button onClick={() => keep.mutate()} disabled={busy || !dirty}>
              {keep.isPending ? "Keeping…" : "Keep as a draft on this computer"}
            </Button>
            <InfoTip term="save--share" align="end" />
            <Button variant="primary" onClick={() => share.mutate()} disabled={busy || blocker !== null}>
              <Icon name="send" size={13} />{" "}
              {share.isPending || edit.status === "saving" ? "Saving and sharing…" : edit.status === "failed" ? "Try again" : "Save & share"}
            </Button>
          </div>
          {blocker && blocker !== "Checking…" && <p className="text-right font-sans text-[12.5px] text-muted">{blocker}</p>}
          {!signedIn && (
            <p className="text-right font-sans text-[12.5px]">
              <Link {...settingsLink("connections", "connection-github")} className="text-ink underline decoration-faint underline-offset-4">
                Open Settings to sign in
              </Link>
            </p>
          )}
          <p className="text-right font-sans text-[12px] text-faint">
            A draft stays in DataLab on this computer only. Save &amp; share runs the tests first, checks it again, commits
            it as you, and pushes it to the lab's pipelines repo on GitHub.
          </p>
        </div>
      </div>
    </section>
  );
}

function Heading({ edit, dirty = false, unchanged = false }: { edit: PipelineEdit; dirty?: boolean; unchanged?: boolean }) {
  const chip = editChip(edit);
  const title =
    edit.files.length === 1
      ? `${edit.files[0].new ? "New file" : "Editing"} ${edit.files[0].path.split("/").pop()}`
      : `Editing ${edit.files.length} files`;
  return (
    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="font-serif text-[21px] leading-tight">{title}</h2>
      <Chip tone="you">Your edit</Chip>
      {EDIT_OPEN.has(edit.status) && dirty ? <Chip tone="attn">changes not kept yet</Chip> : <Chip tone={chip.tone}>{chip.text}</Chip>}
      {unchanged && <Chip>no changes yet</Chip>}
    </header>
  );
}

function CheckLine({ findings, checking }: { findings: PipelineFinding[]; checking: boolean }) {
  const errors = findings.filter((f) => f.severity === "error");
  const others = findings.filter((f) => f.severity !== "error");
  return (
    <div aria-live="polite" className="font-sans text-[12.5px]">
      {findings.length === 0 ? (
        <p className={checking ? "text-faint" : "text-data"}>
          {checking ? "Checking…" : "The check passes: where it can go, the workflow check, and the participant-data scan."}
        </p>
      ) : (
        <>
          <p className="text-muted">
            The check:{" "}
            {[errors.length && `${errors.length} to fix`, others.length && `${others.length} to confirm`].filter(Boolean).join(" · ")}
            {checking && " (checking…)"}
          </p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {[...errors, ...others].slice(0, 8).map((f) => (
              <li key={f.id} className={f.severity === "error" ? "text-danger" : "text-attn"}>
                {f.line ? <span className="font-mono">line {f.line}: </span> : null}
                {f.message}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/**
 * A file changed on GitHub since the edit began: the three versions, and the
 * choice. Reapply merges the person's changes into GitHub's version; where they
 * overlap, the person writes the text to keep. Nothing is shared or overwritten
 * meanwhile.
 */
function Conflict({
  edit,
  texts,
  keepFirst,
  onMoved,
}: {
  edit: PipelineEdit;
  texts: Record<string, string | null>;
  keepFirst: () => Promise<PipelineEdit>;
  onMoved: (edit: PipelineEdit) => void;
}) {
  const [resolutions, setResolutions] = useState<Record<string, string> | null>(null);
  const box = useRef<HTMLElement>(null);
  // Shown when Save & share found it, at the bottom: brought into view within the
  // editor's own scrolling box (scrollIntoView would scroll the whole page too).
  useEffect(() => {
    const el = box.current;
    const scroller = el?.closest<HTMLElement>("[data-edit-scroll]");
    if (el && scroller) {
      scroller.scrollTop += el.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 16;
    }
  }, []);
  const reapply = useMutation({
    mutationFn: async (own?: Record<string, string>) => {
      const kept = await keepFirst();
      return pipelinesApi.reapplyEdit(kept.id, kept.updated_at, own);
    },
    onSuccess: (result) => {
      if (result.merged && Object.keys(result.merged).length > 0) setResolutions(result.merged);
      else {
        setResolutions(null);
        onMoved(result.edit);
      }
    },
  });
  const changed = edit.files.filter((f) => f.upstream_changed);
  const marked = resolutions !== null && Object.values(resolutions).some((t) => /^(<{7}|>{7}) /m.test(t));
  return (
    <section ref={box} aria-label="Changed on GitHub since you started" className="flex flex-col gap-3 rounded-[4px] border border-attn/40 px-4 py-3">
      <p className="font-sans text-[14px] text-attn">
        <Icon name="alert" size={13} className="mr-1 inline translate-y-[-1px]" />
        Someone changed {changed.map((f) => f.path.split("/").pop()).join(", ")} on GitHub since you started editing.
        Nothing was overwritten, and nothing is shared until you choose.
      </p>
      {changed.map((f) => (
        <div key={f.path} className="flex flex-col gap-1">
          <p className="font-mono text-[12.5px] text-ink">{f.path}</p>
          <div className="grid gap-3 @[44rem]:grid-cols-3">
            {(
              [
                ["Your edit", texts[f.path] ?? null],
                ["The version now on GitHub", f.theirs_state === "text" ? (f.theirs ?? "") : null],
                ["Where you started", f.before ?? ""],
              ] as [string, string | null][]
            ).map(([label, value]) => (
              <div key={label} className="flex min-w-0 flex-col gap-1">
                <h3 className="dl-label">{label}</h3>
                {value === null ? (
                  <p className="font-sans text-[13px] text-muted">
                    {label === "Your edit" ? "Deleted in your edit." : f.theirs_state === "deleted" ? "Deleted on GitHub." : "Not text."}
                  </p>
                ) : (
                  <CodeEditor label={`${label}: ${f.path}`} language={languageOf(f.path)} value={value} readOnly className="h-64" />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
      {resolutions === null ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => reapply.mutate(undefined)} disabled={reapply.isPending}>
            Reapply my edit on the new version
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="font-sans text-[13px]">
            Your changes overlap with GitHub's in the marked places. Write the version to keep (remove the{" "}
            <span className="font-mono">{"<<<<<<< ======= >>>>>>>"}</span> lines), then use it.
          </p>
          {Object.entries(resolutions).map(([p, value]) => (
            <CodeEditor
              key={p}
              label={`The version to keep of ${p}`}
              language={languageOf(p)}
              value={value}
              onChange={(next) => setResolutions({ ...resolutions, [p]: next })}
              className="h-72"
            />
          ))}
          <div>
            <Button variant="primary" onClick={() => reapply.mutate(resolutions)} disabled={reapply.isPending || marked}>
              Use this text on the new version
            </Button>
          </div>
        </div>
      )}
      {reapply.error && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          {reapply.error.message}
        </p>
      )}
    </section>
  );
}

function same(a: Record<string, string | null>, b: Record<string, string | null>): boolean {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => k in b && a[k] === b[k]);
}

/** Unkept texts, for the files this edit still has (a file left out stays out). */
function onlyKnown(files: Record<string, string | null>, kept: Record<string, string | null>) {
  const known = Object.entries(files).filter(([p]) => p in kept);
  return known.length > 0 ? Object.fromEntries(known) : kept;
}
