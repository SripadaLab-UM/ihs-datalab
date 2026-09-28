// Send feedback: a bug report or a suggestion, packaged as a support report
// (backend support.py, docs/SUPPORT.md). The person writes what happened and
// what they expected, may add files they choose (a screenshot), and sees
// exactly what's in the report before it's saved: the summary, the
// diagnostics DataLab collects (metadata only, never participant data, query
// results, conversations or keys) and the manifest. Saved reports reopen.
//
// Two ways to the maintainer, both the person's choice:
// - save the ZIP to an export folder (a Dropbox folder is fine), then email
//   it: that's "saved to <folder> (on this computer)", never delivered;
// - send it to the lab's private support repository on GitHub (real DataLab,
//   when set up): the only way it's "delivered", once GitHub has the commit.
// Nothing is ever posted to the public app repository.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router";

import { exportsApi } from "@/api/exports";
import {
  type SupportContents,
  type SupportPreview,
  type SupportRepo,
  type SupportReport,
  type SupportReportDetail,
  type SupportState,
  supportApi,
} from "@/api/support";
import { Button, Chip, Icon, Modal } from "@/components/ui";
import { writeClipboard } from "@/features/settings/sections/DiagnosticsSection";
import { recentTrail } from "@/lib/supportTrail";

export type FeedbackKind = "bug" | "suggestion";

const KINDS: { value: FeedbackKind; label: string }[] = [
  { value: "bug", label: "Bug" },
  { value: "suggestion", label: "Suggestion" },
];

// As the backend allows (support.py).
export const MAX_FILES = 5;
export const MAX_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_FILES_BYTES = 10 * 1024 * 1024;

export interface FeedbackDraft {
  kind: FeedbackKind;
  happened: string;
  expected: string;
  steps: string;
  files: File[];
}

export const EMPTY_DRAFT: FeedbackDraft = { kind: "bug", happened: "", expected: "", steps: "", files: [] };

/** What each state says. Only the support repository's commit is a delivery. */
export const STATE_WORDS: Record<SupportState, string> = {
  saved_locally: "Saved on this computer, in DataLab's data folder",
  saved_to_folder: "Saved to a folder on this computer",
  pending_retry: "Waiting to send to the lab",
  confirmed_delivery: "Delivered to the lab",
  refused: "Not sent to the lab",
};

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Why these files can't be attached, or null. */
export function filesProblem(files: File[]): string | null {
  if (files.length > MAX_FILES) return `Attach at most ${MAX_FILES} files.`;
  const big = files.find((f) => f.size > MAX_FILE_BYTES);
  if (big) return `${big.name} is larger than ${sizeText(MAX_FILE_BYTES)}.`;
  if (files.reduce((n, f) => n + f.size, 0) > MAX_FILES_BYTES)
    return `The files come to more than ${sizeText(MAX_FILES_BYTES)} together.`;
  return null;
}

function base64Of(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(new Error(`${file.name} couldn't be read.`));
    reader.readAsDataURL(file);
  });
}

type View =
  | { name: "form" }
  | { name: "preview"; preview: SupportPreview }
  | { name: "report"; id: string }
  | { name: "list" };

/** The dialog. Its draft lives with whoever opens it, so closing and reopening keeps it. */
export function FeedbackDialog({
  onClose,
  draft,
  onDraft,
}: {
  onClose: () => void;
  draft: FeedbackDraft;
  onDraft: (draft: FeedbackDraft) => void;
}) {
  const [view, setView] = useState<View>({ name: "form" });
  const location = useLocation();
  const client = useQueryClient();
  const reports = useQuery({ queryKey: ["support-reports"], queryFn: supportApi.reports });
  const [problem, setProblem] = useState<string | null>(null);

  const preview = useMutation({
    mutationFn: async () => {
      const attachments = await Promise.all(
        draft.files.map(async (file) => ({ name: file.name, data_base64: await base64Of(file) })),
      );
      return supportApi.preview({
        kind: draft.kind,
        happened: draft.happened,
        expected: draft.kind === "bug" ? draft.expected : "",
        steps: draft.steps,
        // The path only: never the query string or what's after #.
        route: location.pathname,
        user_agent: navigator.userAgent,
        client_trail: recentTrail(),
        attachments,
      });
    },
    onSuccess: (shown) => setView({ name: "preview", preview: shown }),
  });
  const save = useMutation({
    mutationFn: (draftId: string) => supportApi.save(draftId),
    onSuccess: (saved) => {
      client.setQueryData(["support-report", saved.report_id], saved);
      void client.invalidateQueries({ queryKey: ["support-reports"] });
      onDraft(EMPTY_DRAFT); // saved: the next report starts afresh
      setView({ name: "report", id: saved.report_id });
    },
  });

  const title =
    view.name === "form"
      ? "Send feedback"
      : view.name === "preview"
        ? "Check the report"
        : view.name === "list"
          ? "Saved reports"
          : `Report ${view.id}`;
  const saved = reports.data ?? [];

  return (
    <Modal title={title} onClose={onClose} wide={view.name !== "form"}>
      {view.name === "form" && (
        <FeedbackForm
          draft={draft}
          onDraft={(next) => {
            onDraft(next);
            setProblem(null);
          }}
          problem={problem ?? (preview.isError ? preview.error.message : null)}
          busy={preview.isPending}
          savedCount={saved.length}
          onPreview={() => {
            const why = !draft.happened.trim()
              ? draft.kind === "bug"
                ? "Say what happened."
                : "Say what would help."
              : filesProblem(draft.files);
            if (why) setProblem(why);
            else preview.mutate();
          }}
          onSaved={() => setView({ name: "list" })}
        />
      )}
      {view.name === "preview" && (
        <PreviewView
          preview={view.preview}
          files={draft.files}
          busy={save.isPending}
          error={save.isError ? save.error.message : null}
          onBack={() => setView({ name: "form" })}
          onSave={() => save.mutate(view.preview.draft_id)}
        />
      )}
      {view.name === "list" && (
        <SavedList
          reports={saved}
          loading={reports.isLoading}
          onOpen={(id) => setView({ name: "report", id })}
          onNew={() => setView({ name: "form" })}
        />
      )}
      {view.name === "report" && (
        <ReportView id={view.id} onList={() => setView({ name: "list" })} onDeleted={() => setView({ name: "list" })} />
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ the form

function FeedbackForm({
  draft,
  onDraft,
  problem,
  busy,
  savedCount,
  onPreview,
  onSaved,
}: {
  draft: FeedbackDraft;
  onDraft: (draft: FeedbackDraft) => void;
  problem: string | null;
  busy: boolean;
  savedCount: number;
  onPreview: () => void;
  onSaved: () => void;
}) {
  const { kind } = draft;
  const set = (change: Partial<FeedbackDraft>) => onDraft({ ...draft, ...change });
  const picker = useRef<HTMLInputElement>(null);
  return (
    <>
      <p className="flex items-start gap-2 border-l-2 border-attn/60 pl-3 text-sm font-medium text-ink" role="note">
        <Icon name="alert" size={14} className="mt-0.5 shrink-0 text-attn" />
        Don't include participant data: no names, IDs, dates or values from the study database.
      </p>
      <fieldset className="mt-4">
        <legend className="dl-label">Type</legend>
        <div className="mt-1.5 flex gap-2">
          {KINDS.map((choice) => (
            <label
              key={choice.value}
              className={clsx(
                "flex cursor-pointer items-center gap-2 rounded-[3px] border px-3 py-1.5 text-sm transition-colors focus-within:outline focus-within:outline-[1.5px] focus-within:outline-offset-2 focus-within:outline-ink",
                kind === choice.value ? "border-ink" : "border-line hover:border-muted",
              )}
            >
              <input
                type="radio"
                name="feedback-kind"
                value={choice.value}
                checked={kind === choice.value}
                onChange={() => set({ kind: choice.value })}
                className="accent-[var(--color-ink)] focus-visible:outline-none"
              />
              {choice.label}
            </label>
          ))}
        </div>
      </fieldset>
      <Field label={kind === "bug" ? "What happened" : "What would help"}>
        <textarea
          value={draft.happened}
          onChange={(e) => set({ happened: e.target.value })}
          rows={4}
          className={FIELD}
        />
      </Field>
      {kind === "bug" && (
        <Field label="What did you expect">
          <textarea value={draft.expected} onChange={(e) => set({ expected: e.target.value })} rows={2} className={FIELD} />
        </Field>
      )}
      <Field label="Steps to get there (optional)">
        <textarea value={draft.steps} onChange={(e) => set({ steps: e.target.value })} rows={2} className={FIELD} />
      </Field>

      <div className="mt-4">
        <p className="dl-label">Files (optional)</p>
        <p className="mt-1 text-xs text-muted">
          A screenshot, or another file that shows the problem. You're responsible for what's in them: check they show
          no participant data, query results or conversations. DataLab never takes a screenshot itself.
        </p>
        {draft.files.length > 0 && (
          <ul className="mt-2 space-y-1.5" aria-label="Files to include">
            {draft.files.map((file, index) => (
              <li key={`${file.name}-${index}`} className="flex items-center gap-2 text-sm">
                <FileThumb file={file} />
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
                <span className="font-mono text-[11.5px] text-muted">{sizeText(file.size)}</span>
                <Button
                  variant="ghost"
                  className="px-1.5 py-0.5"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => set({ files: draft.files.filter((_, i) => i !== index) })}
                >
                  <Icon name="close" size={13} />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <input
          ref={picker}
          type="file"
          multiple
          aria-label="Choose files to include"
          className="sr-only"
          onChange={(e) => {
            const chosen = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (chosen.length) set({ files: [...draft.files, ...chosen] });
          }}
        />
        <Button className="mt-2" onClick={() => picker.current?.click()} disabled={draft.files.length >= MAX_FILES}>
          <Icon name="attach" size={13} /> Add files…
        </Button>
      </div>

      <details className="mt-4 text-xs text-muted">
        <summary className="cursor-pointer">What DataLab adds by itself</summary>
        <p className="mt-1.5">
          DataLab's version and profile, your system's and browser's versions, which tab is open, recent IDs (this
          conversation, queries, workflow runs, proposals) and the last day's errors and activity: times, types, statuses
          and error codes. Never SQL, query results, conversations, keys or file paths. You see all of it before saving.
        </p>
      </details>

      {problem && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {problem}
        </p>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={onPreview} disabled={busy}>
          {busy ? "Building the report…" : "Preview report"}
        </Button>
        {savedCount > 0 && (
          <Button variant="ghost" onClick={onSaved}>
            Saved reports ({savedCount})
          </Button>
        )}
      </div>
    </>
  );
}

const FIELD =
  "mt-1.5 w-full resize-y rounded-[3px] border border-line bg-field p-2 font-sans text-sm outline-none focus:border-ink";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="mt-4 block">
      <span className="dl-label">{label}</span>
      {children}
    </label>
  );
}

function useObjectUrl(file: File, images = true): string | null {
  const url = useMemo(
    () =>
      (!images || file.type.startsWith("image/")) && typeof URL.createObjectURL === "function"
        ? URL.createObjectURL(file)
        : null,
    [file, images],
  );
  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);
  return url;
}

function FileThumb({ file }: { file: File }) {
  const url = useObjectUrl(file);
  if (!url) return <Icon name="file" size={16} className="shrink-0 text-muted" />;
  return (
    <img
      src={url}
      alt={`Preview of ${file.name}`}
      className="h-8 w-8 shrink-0 rounded-[2px] border border-line object-cover"
    />
  );
}

// ------------------------------------------------------------------ preview

function PreviewView({
  preview,
  files,
  busy,
  error,
  onBack,
  onSave,
}: {
  preview: SupportPreview;
  files: File[];
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onSave: () => void;
}) {
  const images = files.filter((f) => f.type.startsWith("image/"));
  return (
    <>
      <p className="text-sm">
        This is everything in report <span className="font-mono">{preview.report_id}</span> ({preview.zip_name},{" "}
        {sizeText(preview.zip_bytes)}). Nothing has been saved or sent yet.
      </p>
      {preview.warnings.length > 0 && (
        <ul className="mt-3 space-y-1 border-l-2 border-attn/60 pl-3 text-sm text-attn" aria-label="Check before saving">
          {preview.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {images.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2" aria-label="Images included">
          {images.map((file, i) => (
            <ImagePreview key={`${file.name}-${i}`} file={file} />
          ))}
        </div>
      )}
      <Contents contents={preview.contents} />
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="primary" onClick={onSave} disabled={busy}>
          {busy ? "Saving…" : "Save report"}
        </Button>
        <Button onClick={onBack}>Back to edit</Button>
      </div>
    </>
  );
}

function ImagePreview({ file }: { file: File }) {
  const url = useObjectUrl(file);
  if (!url) return null;
  return (
    <figure className="text-xs text-muted">
      <img src={url} alt={`Preview of ${file.name}`} className="max-h-40 max-w-[16rem] rounded-[3px] border border-line" />
      <figcaption className="mt-1 truncate">{file.name}</figcaption>
    </figure>
  );
}

/** Exactly what's in the bundle: its files, the summary, the diagnostics and the manifest. */
export function Contents({ contents }: { contents: SupportContents }) {
  return (
    <div className="mt-4 space-y-4">
      <section aria-label="Files in the report">
        <h3 className="dl-label">Files in the ZIP</h3>
        <table className="mt-1.5 w-full text-left text-[12.5px]">
          <tbody>
            {contents.files.map((file) => (
              <tr key={file.path} className="border-t border-line">
                <td className="py-1 pr-3 font-mono">{file.path}</td>
                <td className="py-1 pr-3 text-right font-mono text-muted">{sizeText(file.bytes)}</td>
                <td className="py-1 font-mono text-muted" title={file.sha256}>
                  sha256 {file.sha256.slice(0, 12)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <Shown label="summary.md" text={contents.summary} />
      <Shown label="diagnostics.json" text={contents.diagnostics} />
      <Shown label="manifest.json" text={contents.manifest} />
    </div>
  );
}

function Shown({ label, text }: { label: string; text: string }) {
  return (
    <section aria-label={label}>
      <h3 className="dl-label">{label}</h3>
      <pre className="mt-1.5 max-h-72 overflow-auto rounded-[3px] border border-line bg-sunken p-2 font-mono text-[11px] leading-snug whitespace-pre-wrap">
        {text}
      </pre>
    </section>
  );
}

// ------------------------------------------------------------------ saved reports

function lastState(report: SupportReport): SupportState {
  return report.states[report.states.length - 1];
}

function stateTone(state: SupportState): "good" | "attn" | "bad" | undefined {
  if (state === "confirmed_delivery") return "good";
  if (state === "pending_retry") return "attn";
  if (state === "refused") return "bad";
  return undefined;
}

function SavedList({
  reports,
  loading,
  onOpen,
  onNew,
}: {
  reports: SupportReport[];
  loading: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <>
      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : reports.length === 0 ? (
        <p className="text-sm text-muted">No saved reports.</p>
      ) : (
        <ul className="divide-y divide-line border-y border-line" aria-label="Saved reports">
          {reports.map((report) => (
            <li key={report.report_id}>
              <button
                type="button"
                className="flex w-full items-center gap-3 px-1 py-2 text-left text-sm hover:bg-sunken"
                onClick={() => onOpen(report.report_id)}
              >
                <span className="font-mono text-[12.5px]">{report.report_id}</span>
                <span className="text-muted">{report.kind === "bug" ? "Bug" : "Suggestion"}</span>
                <span className="min-w-0 flex-1 truncate">{report.headline}</span>
                <Chip tone={stateTone(lastState(report))}>{STATE_WORDS[lastState(report)]}</Chip>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4">
        <Button onClick={onNew}>New report</Button>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ one report

function ReportView({ id, onList, onDeleted }: { id: string; onList: () => void; onDeleted: () => void }) {
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ["support-report", id], queryFn: () => supportApi.report(id) });
  const status = useQuery({ queryKey: ["support-status"], queryFn: supportApi.status });
  const [showContents, setShowContents] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const update = (report: SupportReport) => {
    client.setQueryData<SupportReportDetail>(["support-report", id], (old) => (old ? { ...old, ...report } : old));
    void client.invalidateQueries({ queryKey: ["support-reports"] });
  };
  const remove = useMutation({
    mutationFn: () => supportApi.remove(id),
    onSuccess: () => {
      client.removeQueries({ queryKey: ["support-report", id] });
      void client.invalidateQueries({ queryKey: ["support-reports"] });
      onDeleted();
    },
  });
  const report = detail.data;
  if (detail.isError) return <p className="text-sm text-danger">This report couldn't be opened: {detail.error.message}</p>;
  if (!report) return <p className="text-sm text-muted">Loading…</p>;
  return (
    <>
      <p className="text-sm text-muted">
        {report.kind === "bug" ? "Bug report" : "Suggestion"} · made{" "}
        {report.created_at.replace("T", " ").replace("Z", " UTC")} · {report.zip_name}, {sizeText(report.zip_bytes)}
      </p>
      <States report={report} />

      <FolderOption report={report} onSaved={update} practice={status.data?.practice ?? false} />
      <LabOption report={report} onSent={update} repo={status.data?.github} />

      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <a
          href={supportApi.bundleUrl(id)}
          download={report.zip_name}
          className="inline-flex items-center gap-1.5 rounded-[3px] border border-line px-3 py-1.5 text-[13.5px] font-medium hover:border-ink"
        >
          <Icon name="export" size={13} /> Download ZIP
        </a>
        <Button onClick={() => setShowContents((v) => !v)} aria-expanded={showContents}>
          {showContents ? "Hide contents" : "Show contents"}
        </Button>
        <Button variant="ghost" onClick={onList}>
          Saved reports
        </Button>
        <span className="flex-1" />
        {confirmDelete ? (
          <>
            <span className="text-sm">Delete this report from DataLab?</span>
            <Button variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
              Delete
            </Button>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Keep
            </Button>
          </>
        ) : (
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>
            <Icon name="trash" size={13} /> Delete…
          </Button>
        )}
      </div>
      {showContents && <Contents contents={report.contents} />}
    </>
  );
}

/** Where the report stands, in words. A folder copy is never called delivered. */
function States({ report }: { report: SupportReport }) {
  const github = report.github;
  return (
    <ul className="mt-3 space-y-1.5 text-sm" aria-label="Where this report stands">
      <li className="flex items-center gap-2">
        <Icon name="check" size={13} className="text-data" /> {STATE_WORDS.saved_locally}.
      </li>
      {report.folders.map((folder) => (
        <li key={folder.file} className="flex items-start gap-2">
          <Icon name="folder" size={13} className="mt-1 text-data" />
          <span>
            {folder.saved_to}.{folder.sync_note ? ` ${folder.sync_note}` : ""}
          </span>
        </li>
      ))}
      {github?.state === "pending" && (
        <li className="flex items-start gap-2 text-attn">
          <Icon name="history" size={13} className="mt-1" />
          <span>
            Pending retry: not sent to {github.repo} yet. {github.reason}
          </span>
        </li>
      )}
      {github?.state === "confirmed" && (
        <li className="flex items-start gap-2 text-data">
          <Icon name="send" size={13} className="mt-1" />
          <span>
            Delivered to {github.repo}: GitHub confirmed commit{" "}
            <span className="font-mono">{github.commit_sha?.slice(0, 10) ?? "(unknown)"}</span>.{" "}
            {github.html_url && (
              <a href={github.html_url} target="_blank" rel="noreferrer noopener" className="underline">
                View it on GitHub
              </a>
            )}
          </span>
        </li>
      )}
      {github?.state === "refused" && (
        <li className="flex items-start gap-2 text-danger">
          <Icon name="alert" size={13} className="mt-1" />
          <span>
            Not sent to {github.repo}: {github.reason}
          </span>
        </li>
      )}
    </ul>
  );
}

function FolderOption({
  report,
  onSaved,
  practice,
}: {
  report: SupportReport;
  onSaved: (report: SupportReport) => void;
  practice: boolean;
}) {
  const destinations = useQuery({ queryKey: ["export-destinations"], queryFn: exportsApi.destinations });
  const usable = (destinations.data ?? []).filter((d) => d.available);
  const [chosen, setChosen] = useState<string | null>(null);
  const choice = usable.find((d) => d.id === chosen) ?? usable[0];
  const [copied, setCopied] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (destinationId: string) => supportApi.saveToFolder(report.report_id, destinationId),
    onSuccess: onSaved,
  });
  const last = report.folders[report.folders.length - 1];
  const email = report.email;
  const copy = async (what: string, text: string) =>
    setCopied((await writeClipboard(Promise.resolve(text))) ? what : "refused");
  const whom = email.contact ?? email.to;
  return (
    <section className="mt-5 border-t border-line pt-4" aria-label="Save to a folder, then email it">
      <h3 className="dl-label">Save to a folder, then email it</h3>
      {usable.length === 0 ? (
        <p className="mt-1.5 text-sm text-muted">
          {destinations.isLoading
            ? "Loading folders…"
            : "No export folder is ready. Add one in Settings → Export folders, or use Download ZIP."}
        </p>
      ) : (
        <>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {usable.length > 1 ? (
              <select
                aria-label="Folder"
                value={choice?.id}
                onChange={(e) => setChosen(e.target.value)}
                className="rounded-[3px] border border-line bg-field px-2 py-1.5 text-sm"
              >
                {usable.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            ) : null}
            <Button onClick={() => choice && save.mutate(choice.id)} disabled={!choice || save.isPending}>
              <Icon name="folder" size={13} /> {save.isPending ? "Saving…" : `Save to ${choice?.name}`}
            </Button>
          </div>
          {choice && (
            <p className="mt-1.5 text-xs text-muted">
              {choice.name}: {choice.where}. {choice.location_note}
              {practice ? "" : " Nobody gets it until you email it."}
            </p>
          )}
        </>
      )}
      {save.isError && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {save.error.message}
        </p>
      )}
      {last && (
        <div className="mt-3 space-y-2 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 font-mono text-[12px] break-all">{last.file}</span>
            <Button variant="ghost" className="px-1.5 py-0.5" onClick={() => void copy("path", last.file)}>
              Copy path
            </Button>
          </p>
          <p>
            {email.to
              ? `Next: email this file to ${whom}${whom?.includes(email.to) ? "" : ` at ${email.to}`}.`
              : `Next: send this file to ${whom ?? "the DataLab maintainer (whoever gave you the lab's settings file)"}.`}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {email.mailto && (
              <a
                href={email.mailto}
                className="inline-flex items-center gap-1.5 rounded-[3px] border border-line px-3 py-1.5 text-[13.5px] font-medium hover:border-ink"
              >
                <Icon name="message" size={13} /> Write the email
              </a>
            )}
            <Button variant="ghost" onClick={() => void copy("summary", email.body)}>
              Copy summary
            </Button>
            {copied && copied !== "refused" && (
              <span role="status" className="flex items-center gap-1 text-sm text-data">
                <Icon name="check" size={13} /> Copied
              </span>
            )}
            {copied === "refused" && (
              <span role="status" className="text-sm text-attn">
                The browser didn't let DataLab copy it.
              </span>
            )}
          </div>
          {email.mailto && (
            <p className="text-xs text-muted">
              The email opens in your mail app with the report ID and summary. Attach the file yourself: an email link
              can't attach it.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function LabOption({
  report,
  onSent,
  repo,
}: {
  report: SupportReport;
  onSent: (report: SupportReport) => void;
  repo: SupportRepo | undefined;
}) {
  const checked = useQuery({
    queryKey: ["support-github-check"],
    queryFn: () => supportApi.github(true),
    enabled: !!repo?.available,
    staleTime: 60_000,
  });
  const send = useMutation({ mutationFn: () => supportApi.send(report.report_id), onSuccess: onSent });
  if (!repo) return null;
  const state = report.github?.state;
  const shown = checked.data ?? repo;
  return (
    <section className="mt-5 border-t border-line pt-4" aria-label="Send to the lab">
      <h3 className="dl-label">Send to the lab</h3>
      {!repo.available ? (
        <p className="mt-1.5 text-sm text-muted">{repo.message}</p>
      ) : state === "confirmed" ? (
        <p className="mt-1.5 text-sm text-muted">GitHub has it: nothing more to do.</p>
      ) : (
        <>
          <p className="mt-1.5 text-sm">
            Goes to <span className="font-mono">{shown.repo}</span>
            {shown.private === true ? " (private)" : checked.isLoading ? " (checking it's private…)" : ""}, the lab's
            support repository on GitHub, as your GitHub account.
          </p>
          {shown.message && <p className="mt-1 text-sm text-attn">{shown.message}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              onClick={() => send.mutate()}
              disabled={send.isPending || shown.private === false || !shown.available}
            >
              <Icon name="send" size={13} />{" "}
              {send.isPending ? "Sending…" : state === "pending" || state === "refused" ? "Retry" : "Send to the lab"}
            </Button>
          </div>
          {send.isError && (
            <p role="alert" className="mt-2 text-sm text-danger">
              {send.error.message}
            </p>
          )}
        </>
      )}
    </section>
  );
}
