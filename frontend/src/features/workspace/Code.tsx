// The Code tab: the scripts, SQL and notebooks this conversation's agent wrote
// or changed, each saved version, what changed between them, and the code it
// ran inline without saving a file (backend api/code.py).
//
// Everything shown is a saved copy (a checkpoint), never the live folder, and
// it says so: the latest version reads "Current file in the workspace", any
// other "Saved at turn N · checkpoint 2:41 PM", a snapshot.
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { type CodeFile, type CodeListing, type CodeText, type CodeVersion, codeApi, type InlineCode } from "@/api/code";
import { showStep } from "@/components/chat/showStep";
import { CodeBlock } from "@/components/code/CodeBlock";
import { CodeDiff } from "@/components/code/CodeDiff";
import { type CodeLanguage, codeLanguage, languageLabel } from "@/components/code/languages";
import { Button, Chip, EmptyNote, Icon, Modal } from "@/components/ui";
import { formatBytes } from "@/lib/csv";

/** The listing, refreshed with the conversation's files after each turn (its key starts ["files", id]). */
export function useCode(conversationId: string, all = false) {
  return useQuery({
    queryKey: ["files", conversationId, "code", all],
    queryFn: () => codeApi.list(conversationId, all),
  });
}

const basename = (path: string) => path.split("/").at(-1) ?? path;
const folder = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "");

function time(iso: string): string {
  if (!iso) return "";
  const when = new Date(iso);
  const today = new Date().toDateString() === when.toDateString();
  return today
    ? when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : when.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** "Turn 2 · 2:41 PM", or what the checkpoint was for. */
export function versionName(version: CodeVersion): string {
  if (version.checkpoint === 0) return "As copied in, before the first turn";
  const at = time(version.created_at);
  return version.turn != null ? `Turn ${version.turn}${at ? ` · ${at}` : ""}` : `${version.label}${at ? ` · ${at}` : ""}`;
}

/** The last turn a file changed in, as the list shows it. */
function lastTurn(file: CodeFile): string {
  const last = file.versions.at(-1);
  if (!last || last.checkpoint === 0) return "as copied in";
  return last.turn != null ? `turn ${last.turn}` : last.label.toLowerCase();
}

const GROUPS: { status: CodeFile["status"]; title: string }[] = [
  { status: "new", title: "New" },
  { status: "modified", title: "Modified" },
  { status: "deleted", title: "Deleted since" },
  { status: "unchanged", title: "Unchanged copies" },
];

const STATUS_CHIP: Record<CodeFile["status"], { text: string; tone?: "good" | "attn" | "bad" }> = {
  new: { text: "new", tone: "good" },
  modified: { text: "modified", tone: "attn" },
  deleted: { text: "deleted", tone: "bad" },
  unchanged: { text: "unchanged" },
};

/** The Code tab itself. */
export function CodePanel({ conversationId }: { conversationId: string }) {
  const [all, setAll] = useState(false);
  const listing = useCode(conversationId, all);
  const [open, setOpen] = useState<CodeFile | null>(null);
  if (listing.error) return <p className="text-sm text-danger">{listing.error.message}</p>;
  if (!listing.data) return <p className="text-sm text-muted">Loading…</p>;
  const { files, inline, unchanged } = listing.data;
  if (files.length === 0 && inline.length === 0 && unchanged === 0 && !all) {
    return (
      <EmptyNote icon="code" title="No code yet">
        Scripts, SQL and notebooks the agent writes or changes show up here after each turn, with every saved version.
      </EmptyNote>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <span className="dl-label">
          {files.length} file{files.length === 1 ? "" : "s"} {all ? "of code" : "this conversation wrote or changed"}
        </span>
        {(unchanged > 0 || all) && (
          <label className="flex items-center gap-2 font-sans text-[12.5px] text-muted">
            <input type="checkbox" checked={all} onChange={(event) => setAll(event.target.checked)} />
            {all ? "Showing unchanged repository copies too" : `Show ${unchanged} unchanged repository file${unchanged === 1 ? "" : "s"}`}
          </label>
        )}
      </div>
      {GROUPS.map(({ status, title }) => {
        const group = files.filter((file) => file.status === status);
        if (group.length === 0) return null;
        return (
          <section key={status}>
            <h3 className="dl-label mb-2">{title}</h3>
            <ul className="flex flex-col gap-0.5">
              {group.map((file) => (
                <li key={file.path}>
                  <CodeRow file={file} onOpen={() => setOpen(file)} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      {inline.length > 0 && <InlineGroup items={inline} />}
      {listing.data.more > 0 && <p className="text-xs text-muted">{listing.data.more} more files aren't listed.</p>}
      {open && <CodeViewer conversationId={conversationId} path={open.path} onClose={() => setOpen(null)} />}
    </div>
  );
}

function LanguageTag({ language }: { language: string }) {
  return (
    <span
      aria-hidden
      className="inline-flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[2px] border border-line font-mono text-[10px] font-medium text-muted"
    >
      {language === "r" ? "R" : language === "python" ? "py" : language === "sql" ? "SQL" : language === "notebook" ? "nb" : language === "shell" ? "sh" : language === "yaml" ? "yml" : "{}"}
    </span>
  );
}

function CodeRow({ file, onOpen }: { file: CodeFile; onOpen: () => void }) {
  const chip = STATUS_CHIP[file.status];
  const versions = file.versions.length;
  return (
    <button
      type="button"
      onClick={onOpen}
      title={file.path}
      className="group flex w-full items-center gap-3 rounded-[3px] px-1.5 py-2 text-left hover:bg-surface"
    >
      <LanguageTag language={file.language} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate font-sans text-[13.5px] font-medium group-hover:underline group-hover:decoration-faint group-hover:underline-offset-4">
            {basename(file.path)}
          </span>
          <Chip tone={chip.tone}>{chip.text}</Chip>
        </span>
        <span className="block truncate font-sans text-[11.5px] text-muted">
          {languageLabel(file.language)} · {lastTurn(file)}
          {versions > 1 && ` · ${versions} versions`}
          {folder(file.path) && <span className="font-mono"> · {folder(file.path)}</span>}
        </span>
      </span>
    </button>
  );
}

const PREVIEW_LINES = 6;

function InlineGroup({ items }: { items: InlineCode[] }) {
  return (
    <section>
      <h3 className="dl-label mb-1">Inline code</h3>
      <p className="mb-2 font-sans text-[12px] text-muted">Run without being saved as a file. Each is a step in the chat.</p>
      <ul className="flex flex-col gap-3">
        {items.map((item) => {
          const lines = item.code.split("\n");
          const more = lines.length - PREVIEW_LINES;
          return (
            <li key={item.id} className="rounded-[4px] border border-line p-2.5">
              <div className="mb-1.5 flex flex-wrap items-center gap-2 font-sans text-[12.5px]">
                <span className="font-medium text-ink">
                  {item.language === "shell" ? "Shell commands" : `${languageLabel(item.language)} snippet`}
                </span>
                <span className="text-muted">· turn {item.turn}</span>
                {item.exit_code != null && item.exit_code !== 0 && <Chip tone="bad">stopped with an error</Chip>}
              </div>
              <CodeBlock
                code={lines.slice(0, PREVIEW_LINES).join("\n")}
                language={codeLanguage(item.language)}
                wrap
                className="text-[11.5px]"
                label={`Inline ${languageLabel(item.language)} from turn ${item.turn}`}
              />
              <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
                <span className="font-sans text-[11.5px] text-muted">
                  {more > 0 ? `+${more} more line${more === 1 ? "" : "s"}` : ""}
                </span>
                <Button variant="ghost" className="px-0 py-0 text-[12.5px]" onClick={() => showStep(item.step)}>
                  <Icon name="history" size={13} /> Show in the activity
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** One code file, in a dialog: highlighted, with its versions and what changed. */
export function CodeViewer({ conversationId, path, onClose }: { conversationId: string; path: string; onClose: () => void }) {
  return (
    <Modal
      wide
      title={
        <span className="flex min-w-0 items-center gap-2.5">
          <Icon name="code" size={18} className="shrink-0 text-muted" />
          <span className="min-w-0 truncate">{path}</span>
        </span>
      }
      onClose={onClose}
    >
      <CodeFileView conversationId={conversationId} path={path} />
    </Modal>
  );
}

type View = { kind: "code" } | { kind: "diff"; base: number };

/**
 * A code file (a path in /work) with a version picker: its code, or what
 * changed since another version. `checkpoint` picks the version first shown
 * (the one saved then, or last before it); the latest if not given.
 * `fallback` is shown if DataLab doesn't list the file as code.
 */
export function CodeFileView({
  conversationId,
  path,
  checkpoint,
  fallback,
}: {
  conversationId: string;
  path: string;
  checkpoint?: number | null;
  fallback?: React.ReactNode;
}) {
  const listing = useCode(conversationId, true);
  const file = listing.data?.files.find((f) => f.path === path);
  const [chosen, setChosen] = useState<number | null>(null);
  const [view, setView] = useState<View>({ kind: "code" });
  if (listing.error) return <p className="text-sm text-danger">{listing.error.message}</p>;
  if (!listing.data) return <p className="text-sm text-muted">Loading…</p>;
  if (!file) {
    return fallback ?? <p className="text-sm text-muted">This file isn't in the saved checkpoints yet: files appear after each turn.</p>;
  }
  const versions = file.versions;
  const initial = checkpoint != null ? (versions.filter((v) => v.checkpoint <= checkpoint).at(-1) ?? versions.at(-1)) : versions.at(-1);
  const version = versions.find((v) => v.checkpoint === chosen) ?? initial!;
  const index = versions.indexOf(version);
  const previous = index > 0 ? versions[index - 1] : null;
  const others = versions.filter((v) => v !== version);
  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 font-sans text-[13px]">
        <label className="flex items-center gap-2">
          <span className="text-muted">Version</span>
          <select
            value={version.checkpoint}
            onChange={(event) => {
              setChosen(Number(event.target.value));
              setView({ kind: "code" });
            }}
            className="rounded-[3px] border border-line bg-field px-2 py-1 text-[13px]"
          >
            {[...versions].reverse().map((v) => (
              <option key={v.checkpoint} value={v.checkpoint}>
                {versionName(v)}
                {v === versions.at(-1) && file.current ? " (current)" : ""}
              </option>
            ))}
          </select>
        </label>
        <span className="flex flex-wrap items-center gap-2">
          {view.kind === "diff" ? (
            <Button variant="secondary" className="px-2.5 py-1 text-[12.5px]" onClick={() => setView({ kind: "code" })}>
              <Icon name="code" size={13} /> Show the code
            </Button>
          ) : (
            <Button
              variant="secondary"
              className="px-2.5 py-1 text-[12.5px]"
              disabled={!previous}
              title={previous ? undefined : "This is the first saved version"}
              onClick={() => previous && setView({ kind: "diff", base: previous.checkpoint })}
            >
              Diff with previous
            </Button>
          )}
          {others.length > 0 && (
            <label className="flex items-center gap-1.5">
              <span className="text-muted">Diff with…</span>
              <select
                aria-label="Diff with another version"
                value={view.kind === "diff" ? view.base : ""}
                onChange={(event) => event.target.value !== "" && setView({ kind: "diff", base: Number(event.target.value) })}
                className="rounded-[3px] border border-line bg-field px-2 py-1 text-[13px]"
              >
                <option value="">Choose a version</option>
                {[...others].reverse().map((v) => (
                  <option key={v.checkpoint} value={v.checkpoint}>
                    {versionName(v)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </span>
      </div>
      <SnapshotLabel file={file} version={version} />
      {view.kind === "code" ? (
        <VersionText conversationId={conversationId} file={file} version={version} />
      ) : (
        <VersionDiff conversationId={conversationId} file={file} base={view.base} head={version} versions={versions} />
      )}
    </div>
  );
}

/** Says which copy this is: the current file, or a saved snapshot of an earlier one. */
export function SnapshotLabel({ file, version }: { file: CodeFile; version: CodeVersion }) {
  const current = file.current && version === file.versions.at(-1);
  const saved = version.checkpoint === 0 ? "As copied into the workspace before the first turn" : `Saved at ${version.turn != null ? `turn ${version.turn} · ` : ""}checkpoint ${time(version.created_at) || version.checkpoint}`;
  return (
    <p
      data-testid="code-version-label"
      className={clsx(
        "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-[3px] border px-3 py-1.5 font-sans text-[12.5px]",
        current ? "border-line text-ink" : "border-attn/40 bg-attn-soft text-attn",
      )}
    >
      <Icon name={current ? "file" : "history"} size={13} />
      {current ? (
        <>
          <b className="font-medium">Current file in the workspace</b>{" "}
          <span className="text-muted">· as saved after {version.turn != null ? `turn ${version.turn}` : "the latest checkpoint"}</span>
        </>
      ) : (
        <>
          <b className="font-medium">{saved}</b>{" "}
          <span>
            · snapshot, not the live file{!file.current && version === file.versions.at(-1) ? " (it has since been deleted)" : ""}
          </span>
        </>
      )}
    </p>
  );
}

function TooLarge({ size }: { size: number }) {
  return (
    <p className="rounded-xl bg-sunken p-6 text-center text-sm text-muted">
      This version is too large to show here ({formatBytes(size)}; up to 1 MB is shown). Export the conversation's files to read it.
    </p>
  );
}

function VersionText({ conversationId, file, version }: { conversationId: string; file: CodeFile; version: CodeVersion }) {
  const text = useQuery({
    queryKey: ["code-version", conversationId, file.path, version.checkpoint],
    queryFn: () => codeApi.version(conversationId, file.path, version.checkpoint),
    staleTime: Infinity,
  });
  if (text.error) return <p className="text-sm text-danger">{text.error.message}</p>;
  if (!text.data) return <p className="text-sm text-muted">Loading…</p>;
  return <CodeTextView text={text.data} label={`${file.path}, ${versionName(version)}`} />;
}

/** A version's code, or a notebook's cells. */
export function CodeTextView({ text, label }: { text: CodeText; label: string }) {
  if (text.too_large) return <TooLarge size={text.version.size} />;
  if (text.unreadable) return <p className="text-sm text-muted">DataLab couldn't read this notebook, so it isn't shown.</p>;
  if (text.notebook) return <NotebookView notebook={text.notebook} />;
  return <CodeBlock code={text.text ?? ""} language={codeLanguage(text.language)} lineNumbers label={label} className="rounded-[4px]" />;
}

/** A notebook's cells: code highlighted, Markdown as text, outputs never shown. */
export function NotebookView({ notebook }: { notebook: NonNullable<CodeText["notebook"]> }) {
  const language = codeLanguage(notebook.language);
  return (
    <div className="flex flex-col gap-3">
      {notebook.outputs > 0 && (
        <p className="flex items-center gap-1.5 font-sans text-[12.5px] text-muted">
          <Icon name="shield" size={13} /> {notebook.outputs} output{notebook.outputs === 1 ? "" : "s"} not shown: a notebook's
          outputs can hold data.
        </p>
      )}
      {notebook.cells.map((cell, i) => (
        <div key={i} data-cell={cell.kind}>
          <p className="mb-1 font-mono text-[11px] text-faint">
            [{i + 1}] {cell.kind === "code" ? languageLabel(notebook.language) : cell.kind}
          </p>
          {cell.kind === "code" ? (
            <CodeBlock code={cell.source} language={language} label={`Cell ${i + 1}`} className="rounded-[4px]" />
          ) : (
            <p className="font-serif text-[15px] leading-relaxed whitespace-pre-wrap text-ink">{cell.source}</p>
          )}
          {cell.outputs > 0 && (
            <p className="mt-1 font-sans text-[12px] text-faint">
              {cell.outputs} output{cell.outputs === 1 ? "" : "s"} not shown
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function VersionDiff({
  conversationId,
  file,
  base,
  head,
  versions,
}: {
  conversationId: string;
  file: CodeFile;
  base: number;
  head: CodeVersion;
  versions: CodeVersion[];
}) {
  const diff = useQuery({
    queryKey: ["code-diff", conversationId, file.path, base, head.checkpoint],
    queryFn: () => codeApi.diff(conversationId, file.path, base, head.checkpoint),
    staleTime: Infinity,
  });
  const from = versions.find((v) => v.checkpoint === base);
  if (diff.error) return <p className="text-sm text-danger">{diff.error.message}</p>;
  if (!diff.data) return <p className="text-sm text-muted">Comparing…</p>;
  const d = diff.data;
  const heading = `Changes from ${from ? versionName(from) : `checkpoint ${base}`} to ${versionName(head)}`;
  return (
    <div className="flex flex-col gap-2">
      <p className="flex flex-wrap items-center gap-2 font-sans text-[13px]">
        <span className="font-medium text-ink">{heading}</span>
        {!d.too_large && (
          <>
            <Chip tone="good">+{d.added}</Chip>
            <Chip tone="bad">−{d.removed}</Chip>
          </>
        )}
      </p>
      {d.too_large ? (
        <TooLarge size={Math.max(d.base.size, d.head.size)} />
      ) : d.lines.length === 0 ? (
        <p className="text-sm text-muted">No changes to the code between these versions.</p>
      ) : (
        <>
          <CodeDiff
            lines={d.lines}
            baseText={d.base_text ?? ""}
            headText={d.head_text ?? ""}
            language={codeLanguage(d.language) as CodeLanguage}
            label={heading}
          />
          {d.truncated && <p className="text-xs text-muted">The diff is cut short: it's very long.</p>}
        </>
      )}
    </div>
  );
}

/** How many code files the Outputs tab leaves to this one. */
export function codeCount(listing: CodeListing | undefined): number {
  return listing ? listing.files.length : 0;
}
