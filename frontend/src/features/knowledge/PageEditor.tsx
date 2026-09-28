import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/http";
import { commitUrl, type KbEdit, type KbEditCheck, type KbEntry, knowledgeApi } from "@/api/knowledge";
import { InternalLinks, Markdown } from "@/components/chat/Markdown";
import { CheckFindings, languageOf } from "@/components/chat/ProposalCard";
import { CodeEditor, type EditorDiagnostic } from "@/components/editor/CodeEditor";
import { DiffView } from "@/components/editor/DiffView";
import { Button, Chip, Icon, InfoTip, Tabs } from "@/components/ui";
import { settingsLink } from "@/features/settings/highlight";

import { FrontMatter } from "./FrontMatter";
import { findPage, resolveLink } from "./pages";

export type EditorView = "edit" | "preview" | "review";

/** How long the editor waits after typing before it checks the text again. */
export const CHECK_DELAY_MS = 500;

// What's typed but not yet kept, per edit, in this browser: a reload, a closed
// tab or another page of the Knowledge tab doesn't lose it. The kept draft is
// DataLab's (the database, on this computer), for every browser.
const UNSAVED = "datalab:kb:unsaved:";

export function readUnsaved(editId: string): string | null {
  try {
    return localStorage.getItem(UNSAVED + editId);
  } catch {
    return null;
  }
}

function writeUnsaved(editId: string, text: string | null) {
  try {
    if (text === null) localStorage.removeItem(UNSAVED + editId);
    else localStorage.setItem(UNSAVED + editId, text);
  } catch {
    // Storage can be unavailable (a private window): the text lasts while the page is open.
  }
}

/** The text as it was last checked: `value` once it has stayed the same for `delay`. */
function useSettled(value: string, delay: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/**
 * Editing a page by hand, with no AI request: its Markdown and front matter,
 * a preview as the page reads, the check as Save & share will run it, and
 * the changes against where the edit started. "Keep as a draft" keeps it on
 * this computer only; Save & share commits it as the person and pushes it to
 * GitHub. If the page changed on GitHub meanwhile, the three versions are
 * shown before anything can be shared.
 */
export function PageEditor({
  path,
  editId,
  isNew = false,
  initialView = "edit",
  repo,
  entries,
  signedIn,
  onOpen,
  onClose,
}: {
  path: string;
  /** An edit to open (a suggestion accepted in a conversation); else the page's open edit, or a new one. */
  editId?: string;
  isNew?: boolean;
  initialView?: EditorView;
  repo: string | null | undefined;
  entries: KbEntry[];
  signedIn: boolean;
  onOpen: (path: string) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const key = ["kb-edit", editId ?? path];
  const loaded = useQuery({
    queryKey: key,
    queryFn: () => (editId ? knowledgeApi.getEdit(editId) : knowledgeApi.startEdit(path, isNew)),
    retry: false,
    staleTime: Infinity,
  });
  if (loaded.error) {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert" className="font-sans text-[13.5px] text-danger">
          {loaded.error.message}
        </p>
        <div>
          <Button onClick={onClose}>Back to the page</Button>
        </div>
      </div>
    );
  }
  if (!loaded.data) return <p className="font-sans text-[13px] text-muted">Opening {path} for editing…</p>;
  return (
    <Editor
      key={loaded.data.id}
      edit={loaded.data}
      onEdit={(next) => {
        queryClient.setQueryData(key, next);
        queryClient.setQueryData(["kb-edit-by-id", next.id], next);
        queryClient.invalidateQueries({ queryKey: ["kb-edits"] });
      }}
      initialView={initialView}
      repo={repo}
      entries={entries}
      signedIn={signedIn}
      onOpen={onOpen}
      onClose={onClose}
    />
  );
}

function Editor({
  edit,
  onEdit,
  initialView,
  repo,
  entries,
  signedIn,
  onOpen,
  onClose,
}: {
  edit: KbEdit;
  onEdit: (edit: KbEdit) => void;
  initialView: EditorView;
  repo: string | null | undefined;
  entries: KbEntry[];
  signedIn: boolean;
  onOpen: (path: string) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const open = ["draft", "conflict", "check_failed", "failed"].includes(edit.status);
  // What's typed here, from this browser's unsaved copy if there is one.
  const [restored] = useState(() => {
    const unsaved = readUnsaved(edit.id);
    return open && unsaved !== null && unsaved !== edit.text ? unsaved : null;
  });
  const [text, setText] = useState(restored ?? edit.text);
  const dirty = text !== edit.text;
  useEffect(() => {
    if (open) writeUnsaved(edit.id, text !== edit.text ? text : null);
  }, [edit.id, edit.text, text, open]);
  const [view, setView] = useState<EditorView>(initialView);
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [discarding, setDiscarding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const language = languageOf(edit.path);
  const markdown = edit.path.endsWith(".md");

  const settled = useSettled(text, CHECK_DELAY_MS);
  const check = useQuery({
    queryKey: ["kb-edit-check", edit.id, settled],
    queryFn: () => knowledgeApi.checkEdit(edit.id, settled),
    placeholderData: (previous) => previous,
    enabled: open,
  });
  // The check is of what's on screen: not a moment before.
  const current: KbEditCheck | undefined = check.data && settled === text && !check.isPlaceholderData ? check.data : undefined;
  const findings = useMemo(() => check.data?.findings ?? [], [check.data]);
  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      findings
        .filter((f) => f.line)
        .map((f) => ({ line: f.line!, message: f.message, severity: f.severity === "error" ? "error" : "warning" })),
    [findings],
  );

  const refresh = () => {
    for (const k of ["knowledge-status", "kb-pages", "kb-page", "kb-history"]) queryClient.invalidateQueries({ queryKey: [k] });
  };
  const keepNow = async (): Promise<KbEdit> => {
    if (!dirty) return edit;
    const kept = await knowledgeApi.keepEdit(edit.id, text, edit.updated_at);
    writeUnsaved(edit.id, null);
    onEdit(kept);
    return kept;
  };
  const stale = (error: Error) => {
    if (error instanceof ApiError && error.status === 409) {
      queryClient.invalidateQueries({ queryKey: ["kb-edit"] });
    }
  };
  const keep = useMutation({
    mutationFn: keepNow,
    onSuccess: () => setNotice("Saved on this computer (not shared)."),
    onError: stale,
  });
  const share = useMutation({
    mutationFn: async () => {
      const shown = current;
      if (!shown) throw new Error("Wait for the check to finish, then save.");
      const kept = await keepNow();
      return knowledgeApi.shareEdit(kept.id, [...confirmed], kept.text_sha256, shown.findings.map((f) => f.id));
    },
    onSuccess: (next) => {
      onEdit(next);
      setNotice(null);
      if (next.status === "saved") {
        writeUnsaved(next.id, null);
        refresh();
      }
    },
    onError: stale,
  });
  const discard = useMutation({
    mutationFn: () => knowledgeApi.discardEdit(edit.id),
    onSuccess: (next) => {
      writeUnsaved(edit.id, null);
      onEdit(next);
      onClose();
    },
  });
  const busy = keep.isPending || share.isPending || discard.isPending;

  if (edit.status === "saved") {
    const url = edit.commit ? commitUrl(repo, edit.commit) : null;
    return (
      <section aria-label={`Editing ${edit.path}`} className="flex flex-col gap-4">
        <EditorHeading edit={edit} />
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
          Committed as you and pushed to the lab's knowledge base. Other conversations see it from their next copy.
        </p>
        <div>
          <Button variant="primary" onClick={onClose}>
            Back to the page
          </Button>
        </div>
      </section>
    );
  }
  if (!open) {
    return (
      <section className="flex flex-col gap-3">
        <EditorHeading edit={edit} />
        <p className="font-sans text-[13.5px] text-muted">This draft was discarded. Nothing was shared.</p>
        <div>
          <Button onClick={onClose}>Back to the page</Button>
        </div>
      </section>
    );
  }

  const errors = findings.filter((f) => f.severity === "error").length;
  const unconfirmed = findings.filter((f) => f.severity === "data" && !confirmed.has(f.id)).length;
  const unchanged = !edit.new_page && text === (edit.before ?? "");
  const blocker = edit.upstream_changed
    ? "The page changed on GitHub since you started: see the versions above first."
    : !signedIn
      ? "Sign in to GitHub to share."
      : unchanged
        ? "No changes yet."
        : !current
          ? "Checking…"
          : errors
            ? "Fix what the check found (Review changes) before sharing."
            : unconfirmed
              ? "Check each possible participant-data finding (Review changes) before sharing."
              : null;
  const result = edit.result;
  const follow = (href: string) => {
    const target = resolveLink(edit.path, href);
    const found = target ? findPage(entries, target) : undefined;
    return found ? () => onOpen(found.path) : null;
  };

  return (
    <section aria-label={`Editing ${edit.path}`} className="flex flex-col gap-4">
      <EditorHeading edit={edit} dirty={dirty} />
      {edit.origin && <Origin origin={edit.origin} />}
      {restored !== null && (
        <p role="status" className="font-sans text-[12.5px] text-attn">
          Restored changes you hadn't kept yet, from this browser.
        </p>
      )}
      {edit.upstream_changed && (
        <Conflict
          edit={edit}
          text={text}
          keepFirst={keepNow}
          onMoved={(next) => {
            onEdit(next);
            setText(next.text);
            writeUnsaved(next.id, null);
          }}
        />
      )}

      <Tabs
        tabs={[
          { id: "edit" as const, label: "Edit" },
          ...(markdown ? [{ id: "preview" as const, label: "Preview" }] : []),
          { id: "review" as const, label: "Review changes" },
        ]}
        value={view}
        onChange={setView}
      />

      {view === "edit" && (
        <div className="flex flex-col gap-2">
          <CodeEditor
            label={`${edit.path}: its Markdown and front matter`}
            language={language}
            value={text}
            onChange={setText}
            diagnostics={diagnostics}
            className="h-[min(60vh,36rem)]"
          />
          <CheckLine findings={findings} checking={!current} />
        </div>
      )}
      {view === "preview" && (
        <div data-testid="kb-preview" className="flex flex-col gap-5">
          {check.data?.front_matter ? (
            <FrontMatter fields={check.data.front_matter} entries={entries} onOpen={onOpen} />
          ) : (
            check.data && <p className="font-sans text-[13px] text-attn">The front matter can't be read yet: see the check.</p>
          )}
          <InternalLinks value={follow}>
            <Markdown text={check.data?.body ?? text} />
          </InternalLinks>
        </div>
      )}
      {view === "review" && (
        <div className="flex flex-col gap-4">
          {(current?.notes ?? []).map((note) => (
            <p key={note} className="flex items-baseline gap-1.5 font-sans text-[13px] text-attn">
              <Icon name="alert" size={12} className="translate-y-[1px]" /> {note}
            </p>
          ))}
          <p className="font-sans text-[12.5px] text-muted">
            {edit.new_page ? "A new page." : "Against where you started (−), what Save & share would commit (+)."}{" "}
            reviewed_by and reviewed_on are as DataLab will set them.
          </p>
          <DiffView
            label={`${edit.path}: your changes`}
            original={edit.before ?? ""}
            modified={current?.shared ?? text}
            language={language}
            layout="unified"
            className="max-h-[32rem]"
          />
          <CheckFindings
            findings={findings}
            confirmed={confirmed}
            onConfirm={(id, yes) => {
              const next = new Set(confirmed);
              if (yes) next.add(id);
              else next.delete(id);
              setConfirmed(next);
            }}
          />
        </div>
      )}

      {result && edit.status !== "draft" && (
        <p role="alert" className={clsx("font-sans text-[13px]", edit.status === "conflict" ? "text-attn" : "text-danger")}>
          {result.message}
        </p>
      )}
      {notice && !dirty && (
        <p role="status" className="font-sans text-[13px] text-muted">
          {notice}
        </p>
      )}
      {(keep.error || share.error || discard.error) && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          {(keep.error ?? share.error ?? discard.error)?.message}
        </p>
      )}

      <div className="flex flex-col gap-2 border-t border-line pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Close
          </Button>
          {discarding ? (
            <Button variant="danger" onClick={() => discard.mutate()} disabled={busy}>
              Discard this draft
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
            {share.isPending ? "Saving and sharing…" : edit.status === "failed" ? "Try again" : "Save & share"}
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
          A draft stays in DataLab on this computer only. Save & share checks it again, commits it as you, and pushes it
          to the lab's knowledge base on GitHub.
        </p>
      </div>
    </section>
  );
}

function EditorHeading({ edit, dirty = false }: { edit: KbEdit; dirty?: boolean }) {
  const kept = edit.status !== "saved" && edit.status !== "discarded";
  return (
    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="font-serif text-[22px] leading-tight">{edit.new_page ? "New page" : "Editing"}</h2>
      <span className="font-mono text-[12.5px] text-muted">{edit.path}</span>
      {kept && (dirty ? <Chip tone="attn">changes not kept yet</Chip> : <Chip tone="you">draft on this computer · not shared</Chip>)}
      {edit.status === "saved" && <Chip tone="good">shared to GitHub</Chip>}
      {edit.status === "conflict" && <Chip tone="attn">changed on GitHub since</Chip>}
    </header>
  );
}

/** Where a suggested update came from: its conversation and the queries behind it. */
function Origin({ origin }: { origin: NonNullable<KbEdit["origin"]> }) {
  return (
    <div className="rounded-[3px] border border-line bg-sunken px-3 py-2 font-sans text-[12.5px] text-muted">
      <p>
        <span className="text-ink">From a suggested Knowledge update</span> in{" "}
        <Link to={`/workspace/${origin.conversation_id}`} className="text-ink underline decoration-faint underline-offset-4">
          its conversation
        </Link>
        {origin.reason && <>: {origin.reason}</>}
      </p>
      {origin.evidence.length > 0 && (
        <p className="mt-0.5">
          Evidence: {origin.evidence.map((e) => `${e.tables.join(", ") || "a query"} (${e.query_id})`).join("; ")}. The
          queries stay in that conversation; the page itself cites its own evidence.
        </p>
      )}
    </div>
  );
}

function CheckLine({ findings, checking }: { findings: KbEditCheck["findings"]; checking: boolean }) {
  const errors = findings.filter((f) => f.severity === "error");
  const data = findings.filter((f) => f.severity === "data");
  const warnings = findings.filter((f) => f.severity === "warning");
  return (
    <div aria-live="polite" className="font-sans text-[12.5px]">
      {findings.length === 0 ? (
        <p className={checking ? "text-faint" : "text-data"}>
          {checking ? "Checking…" : "The check passes: front matter, links, tables and columns, and the participant-data scan."}
        </p>
      ) : (
        <>
          <p className="text-muted">
            The check: {[errors.length && `${errors.length} to fix`, data.length && `${data.length} possible participant data`, warnings.length && `${warnings.length} warning${warnings.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ")}
            {checking && " (checking…)"}
          </p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {[...errors, ...data, ...warnings].slice(0, 8).map((f) => (
              <li key={f.id} className={f.severity === "error" ? "text-danger" : f.severity === "data" ? "text-attn" : "text-muted"}>
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
 * The page changed on GitHub since the edit began: the three versions, and
 * the choice. Reapply merges the person's changes into GitHub's version; if
 * they overlap, both are shown side by side with the overlaps marked, and the
 * person writes the text to keep. Nothing is shared or overwritten meanwhile.
 */
function Conflict({
  edit,
  text,
  keepFirst,
  onMoved,
}: {
  edit: KbEdit;
  text: string;
  keepFirst: () => Promise<KbEdit>;
  onMoved: (edit: KbEdit) => void;
}) {
  const [sideBySide, setSideBySide] = useState(false);
  const [resolution, setResolution] = useState<string | null>(null);
  const language = languageOf(edit.path);
  const theirs = edit.theirs_state === "text" ? (edit.theirs ?? "") : null;
  const reapply = useMutation({
    mutationFn: async (own?: string) => {
      const kept = await keepFirst();
      return knowledgeApi.reapplyEdit(kept.id, kept.updated_at, own);
    },
    onSuccess: (result) => {
      if (result.merged !== null && result.merged !== undefined) {
        setResolution(result.merged);
        setSideBySide(true);
      } else {
        setResolution(null);
        onMoved(result.edit);
      }
    },
  });
  const columns: [string, string | null][] = [
    ["Your edit", text],
    ["The version now on GitHub", theirs],
    ["Where you started", edit.before ?? ""],
  ];
  return (
    <section aria-label="Changed on GitHub since you started" className="flex flex-col gap-3 rounded-[4px] border border-attn/40 px-4 py-3">
      <p className="font-sans text-[14px] text-attn">
        <Icon name="alert" size={13} className="mr-1 inline translate-y-[-1px]" />
        {edit.theirs_state === "deleted"
          ? "Someone deleted this page on GitHub since you started editing."
          : "Someone changed this page on GitHub since you started editing."}{" "}
        Nothing was overwritten, and nothing is shared until you choose.
      </p>
      <div className="grid gap-3 @[56rem]:grid-cols-3">
        {columns.map(([label, value]) => (
          <div key={label} className="flex min-w-0 flex-col gap-1">
            <h3 className="dl-label">{label}</h3>
            {value === null ? (
              <p className="font-sans text-[13px] text-muted">{edit.theirs_state === "deleted" ? "Deleted on GitHub." : "Not text."}</p>
            ) : (
              <CodeEditor label={`${label}: ${edit.path}`} language={language} value={value} readOnly className="h-64" />
            )}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => reapply.mutate(undefined)} disabled={reapply.isPending || theirs === null}>
          Reapply my edit on the new version
        </Button>
        <Button onClick={() => setSideBySide(!sideBySide)} aria-expanded={sideBySide}>
          {sideBySide ? "Hide side by side" : "Open both side by side"}
        </Button>
      </div>
      {reapply.error && (
        <p role="alert" className="font-sans text-[13px] text-danger">
          {reapply.error.message}
        </p>
      )}
      {sideBySide && (
        <div className="flex flex-col gap-2">
          <p className="font-sans text-[12.5px] text-muted">GitHub's version now (left) and yours (right).</p>
          <DiffView label={`${edit.path}: GitHub's version and yours`} original={theirs ?? ""} modified={text} language={language} layout="split" className="max-h-[28rem]" />
          <p className="font-sans text-[13px]">
            {resolution !== null
              ? "Your changes overlap with GitHub's in the marked places. Write the version to keep (remove the <<<<<<< ======= >>>>>>> lines), then use it."
              : "Write the version to keep, starting from yours, then use it on GitHub's version."}
          </p>
          <CodeEditor
            label={`The version to keep of ${edit.path}`}
            language={language}
            value={resolution ?? text}
            onChange={setResolution}
            className="h-72"
          />
          <div>
            <Button
              variant="primary"
              onClick={() => reapply.mutate(resolution ?? text)}
              disabled={reapply.isPending || /^(<{7}|>{7}) /m.test(resolution ?? "")}
            >
              Use this text on the new version
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
