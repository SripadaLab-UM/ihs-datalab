import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { commitUrl, type Finding, knowledgeApi, type ProposalDetail, type ProposalFile } from "@/api/knowledge";
import { CodeEditor, type EditorLanguage } from "@/components/editor/CodeEditor";
import { DiffView } from "@/components/editor/DiffView";
import { Button, Chip, Icon } from "@/components/ui";

import { checkChips, proposalState, proposalTitle, saveBlocker } from "./activity";
import type { KbProposalItem } from "./transcript";

/** The editor's language for a knowledge-base file. */
export function languageOf(path: string): EditorLanguage {
  const ext = path.toLowerCase().split(".").at(-1);
  if (ext === "md") return "markdown";
  if (ext === "yml" || ext === "yaml") return "yaml";
  if (ext === "r") return "r";
  if (ext === "sql") return "sql";
  return "text";
}

const short = (commit: string) => commit.slice(0, 7);

/**
 * Knowledge edits the agent proposed after a turn. The person reads the diff
 * of each file, may edit or leave out any of them, and then saves and shares
 * the result (a commit on GitHub, as them) or discards it. Nothing is saved
 * without that click.
 */
export function ProposalCard({ proposal }: { proposal: KbProposalItem }) {
  const queryClient = useQueryClient();
  const key = ["kb-proposal", proposal.id];
  const [open, setOpen] = useState(() => proposalState(proposal.status).actionable);
  const detail = useQuery({ queryKey: key, queryFn: () => knowledgeApi.proposal(proposal.id), enabled: open });
  // The chat's events say when it changed elsewhere (another window, the agent's next turn).
  useEffect(() => {
    queryClient.invalidateQueries({ queryKey: ["kb-proposal", proposal.id] });
  }, [proposal.status, proposal.id, queryClient]);
  const status = detail.data?.proposal.status ?? proposal.status;
  const state = proposalState(status, detail.data?.proposal.result?.message ?? proposal.message);
  const commit = detail.data?.proposal.commit ?? proposal.commit;
  const knowledge = useQuery({ queryKey: ["knowledge-status"], queryFn: knowledgeApi.status, enabled: state.actionable || Boolean(commit) });
  // Once it's decided (saved, discarded, replaced), the review folds away; it can be opened again.
  const decided = !state.actionable;
  useEffect(() => {
    if (decided) setOpen(false);
  }, [decided]);

  return (
    <section
      aria-label={proposalTitle(proposal)}
      className={clsx("border-l py-1 pl-5 text-[14px]", state.actionable ? "border-you" : "border-line")}
    >
      <div className="flex flex-wrap items-baseline gap-2.5">
        <Icon name="book" size={15} className="translate-y-[2px] text-muted" />
        <h3 className="font-serif text-[21px] font-normal">{proposalTitle(proposal)}</h3>
        <Chip tone={state.tone}>{state.label}</Chip>
      </div>
      <p className="mt-1 max-w-[62ch] font-serif text-[16px] leading-relaxed text-muted italic">{state.text}</p>
      {status === "saved" && commit && (
        <p className="mt-1 font-sans text-[13px]">
          <CommitLink repo={knowledge.data?.name} commit={commit} />
          {detail.data?.proposal.decided_by && <span className="text-muted"> · by @{detail.data.proposal.decided_by}</span>}
        </p>
      )}

      {/* Open, each file has its own review below. */}
      {!open && (
      <ul className="mt-3 flex flex-col gap-1 font-sans text-[13px]">
        {proposal.files.map((file) => (
          <li key={file.path} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="font-mono text-[12.5px] text-ink">{file.path}</span>
            <span className="text-muted">{file.change}</span>
            <span className="font-mono text-[11.5px] text-muted tabular">
              +{file.added} −{file.removed}
            </span>
            {file.flags.map((flag) => (
              <Chip key={flag} tone="attn">
                {flag}
              </Chip>
            ))}
          </li>
        ))}
      </ul>
      )}
      {/* The check as it was when proposed; the review below runs it on the text as it is now. */}
      {proposal.files.length > 0 && !open && state.actionable && (
        <p className="mt-2 flex flex-wrap gap-1.5">
          {checkChips(proposal.check).map((chip) => (
            <Chip key={chip.text} tone={chip.tone}>
              {chip.text}
            </Chip>
          ))}
        </p>
      )}
      {proposal.refused.length > 0 && <Refused refused={proposal.refused} />}

      {proposal.files.length > 0 &&
        (open ? (
          <div className="mt-4">
            {detail.isPending && <p className="font-sans text-[13px] text-muted">Loading the changes…</p>}
            {detail.error && <p className="font-sans text-[13px] text-danger">{detail.error.message}</p>}
            {detail.data && (
              <Review
                detail={detail.data}
                actionable={state.actionable && status !== "saving"}
                signedIn={knowledge.data?.signed_in ?? false}
                onDetail={(next) => queryClient.setQueryData(key, next)}
              />
            )}
            {!state.actionable && (
              <Button variant="ghost" className="mt-2 px-0 text-[12.5px]" onClick={() => setOpen(false)}>
                Hide the changes
              </Button>
            )}
          </div>
        ) : (
          <Button variant="ghost" className="mt-2 px-0 text-[12.5px]" onClick={() => setOpen(true)}>
            {state.actionable ? "Review the changes" : "Show the changes"}
          </Button>
        ))}
    </section>
  );
}

function CommitLink({ repo, commit }: { repo: string | null | undefined; commit: string }) {
  const url = commitUrl(repo, commit);
  if (!url) return <span className="font-mono text-[12.5px]">Commit {short(commit)}</span>;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
      Commit <span className="font-mono text-[12.5px]">{short(commit)}</span> on GitHub <Icon name="open" size={12} />
    </a>
  );
}

function Refused({ refused }: { refused: { path: string; reason: string }[] }) {
  return (
    <div className="mt-3">
      <p className="dl-label">Not proposed</p>
      <p className="mt-0.5 font-sans text-[12.5px] text-muted">The agent changed these too, but they can't be shared from here.</p>
      <ul className="mt-1.5 flex flex-col gap-1 font-sans text-[13px]">
        {refused.map((r) => (
          <li key={r.path} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[12.5px] text-ink">{r.path}</span>
            <span className="text-attn">{r.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Each file's changes, the check, the result of the last save, and the buttons. */
function Review({
  detail,
  actionable,
  signedIn,
  onDetail,
}: {
  detail: ProposalDetail;
  actionable: boolean;
  signedIn: boolean;
  onDetail: (detail: ProposalDetail) => void;
}) {
  const proposal = detail.proposal;
  const queryClient = useQueryClient();
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const accept = useMutation({
    mutationFn: () => knowledgeApi.accept(proposal.id, [...confirmed]),
    onSuccess: (next) => {
      onDetail(next);
      // Shared: the Knowledge tab's pages, history and status move on.
      if (next.proposal.status === "saved") {
        for (const key of ["knowledge-status", "kb-pages", "kb-page", "kb-history"]) queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  const reject = useMutation({ mutationFn: () => knowledgeApi.reject(proposal.id), onSuccess: onDetail });
  const busy = accept.isPending || reject.isPending;
  const sharing = detail.files.filter((f) => !f.left_out && f.after !== f.before).length;
  const unresolved = detail.files.filter((f) => f.conflict && !f.edited && !f.left_out).length;
  const blocker = saveBlocker({ findings: detail.findings, confirmed, signedIn, sharing, unresolved });
  const result = proposal.result;

  return (
    <div className="flex flex-col gap-5">
      {detail.files.map((file) => (
        <FileReview key={file.path} proposalId={proposal.id} file={file} actionable={actionable && !busy} onDetail={onDetail} />
      ))}

      <CheckFindings
        findings={detail.findings}
        confirmed={confirmed}
        onConfirm={
          actionable
            ? (id, yes) => {
                const next = new Set(confirmed);
                if (yes) next.add(id);
                else next.delete(id);
                setConfirmed(next);
              }
            : undefined
        }
      />

      {result && proposal.status === "check_failed" && result.after_rebase && result.findings.length > 0 && (
        <div>
          <p className="dl-label">After bringing in others' changes</p>
          <p className="mt-0.5 font-sans text-[12.5px] text-muted">
            Someone else's change on GitHub made these fail. Nothing was shared.
          </p>
          <FindingList findings={result.findings} />
        </div>
      )}
      {result && proposal.status === "conflict" && result.conflicts.length > 0 && (
        <p className="font-sans text-[13px] text-attn">
          Changed on GitHub since: {result.conflicts.join(", ")}. Each is compared with GitHub's version above.
        </p>
      )}

      {actionable && (
        <div className="flex flex-col gap-2">
          {blocker && <p className="font-sans text-[13px] text-muted">{blocker}</p>}
          {!signedIn && (
            <p className="font-sans text-[13px]">
              <Link to="/settings" className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
                Open Settings to sign in
              </Link>
            </p>
          )}
          {(accept.error || reject.error) && (
            <p className="font-sans text-[13px] text-danger">{(accept.error ?? reject.error)?.message}</p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button onClick={() => reject.mutate()} disabled={busy}>
              Discard
            </Button>
            <Button variant="primary" onClick={() => accept.mutate()} disabled={busy || blocker !== null}>
              {accept.isPending ? "Saving and sharing…" : "Save & share"}
            </Button>
          </div>
          <p className="text-right font-sans text-[12px] text-faint">
            Save & share checks it again, commits it as you, and pushes it to the lab's knowledge base on GitHub.
          </p>
        </div>
      )}
    </div>
  );
}

/** One file: its diff, and (while it can still change) the person's own edit, or leaving it out. */
function FileReview({
  proposalId,
  file,
  actionable,
  onDetail,
}: {
  proposalId: string;
  file: ProposalFile;
  actionable: boolean;
  onDetail: (detail: ProposalDetail) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(file.after ?? "");
  const edit = useMutation({
    mutationFn: (change: { text?: string | null; reset?: boolean }) =>
      change.reset ? knowledgeApi.edit(proposalId, {}, [file.path]) : knowledgeApi.edit(proposalId, { [file.path]: change.text ?? null }),
    onSuccess: (detail) => {
      onDetail(detail);
      setEditing(false);
    },
  });
  const language = languageOf(file.path);
  const startEditing = () => {
    setDraft(file.after ?? "");
    setEditing(true);
  };

  return (
    <article aria-label={file.path} className="flex flex-col gap-2">
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h4 className="font-mono text-[13px] text-ink">{file.path}</h4>
        <span className="font-sans text-[12.5px] text-muted">{file.change}</span>
        {file.edited && <Chip tone="you">your edit</Chip>}
        {file.left_out && <Chip>left out</Chip>}
        {file.conflict && <Chip tone="attn">changed on GitHub since</Chip>}
      </header>
      {file.flags.map((flag) => (
        <p key={flag} className="flex items-baseline gap-1.5 font-sans text-[12.5px] text-attn">
          <Icon name="alert" size={12} className="translate-y-[1px]" /> {flag}
        </p>
      ))}

      {file.left_out ? (
        <p className="font-sans text-[13px] text-muted">Left out: this file stays as it is on GitHub.</p>
      ) : file.conflict ? (
        <>
          <p className="font-sans text-[13px] text-muted">
            Someone else changed this file on GitHub since. Below, GitHub's version now (−) is compared with yours (+).
            Resolve it: write what it should say, then Save & share again.
          </p>
          <DiffView
            label={`${file.path}: GitHub's version now, and yours`}
            original={file.theirs ?? ""}
            modified={file.after ?? ""}
            language={language}
            layout="unified"
            className="max-h-[28rem]"
          />
        </>
      ) : (
        <DiffView
          label={`${file.path}: the changes`}
          original={file.before ?? ""}
          modified={file.after ?? ""}
          language={language}
          layout="unified"
          className="max-h-[28rem]"
        />
      )}

      {editing && (
        <div className="flex flex-col gap-2">
          <CodeEditor label={`Your version of ${file.path}`} language={language} value={draft} onChange={setDraft} className="h-[22rem]" />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => edit.mutate({ text: draft })} disabled={edit.isPending}>
              {file.conflict ? "Keep this version" : "Keep my edit"}
            </Button>
            {file.conflict && file.theirs !== null && (
              <Button onClick={() => setDraft(file.theirs ?? "")} disabled={edit.isPending}>
                Start from GitHub's version
              </Button>
            )}
            <Button variant="ghost" onClick={() => setEditing(false)} disabled={edit.isPending}>
              Cancel
            </Button>
          </div>
          <p className="font-sans text-[12px] text-faint">Kept here until you save and share. Nothing goes to GitHub yet.</p>
        </div>
      )}
      {edit.error && <p className="font-sans text-[13px] text-danger">{edit.error.message}</p>}

      {actionable && !editing && (
        <div className="flex flex-wrap gap-1">
          {!file.left_out && file.change !== "deleted" && (
            <Button variant="ghost" className="px-2 text-[12.5px]" onClick={startEditing}>
              <Icon name="pen" size={12} /> {file.conflict ? "Resolve" : "Edit"}
            </Button>
          )}
          {file.left_out ? (
            <Button variant="ghost" className="px-2 text-[12.5px]" onClick={() => edit.mutate({ reset: true })} disabled={edit.isPending}>
              Put it back
            </Button>
          ) : (
            <Button variant="ghost" className="px-2 text-[12.5px]" onClick={() => edit.mutate({ text: null })} disabled={edit.isPending}>
              Leave out
            </Button>
          )}
          {file.edited && (
            <Button variant="ghost" className="px-2 text-[12.5px]" onClick={() => edit.mutate({ reset: true })} disabled={edit.isPending}>
              Use the agent's version
            </Button>
          )}
        </div>
      )}
    </article>
  );
}

/** The check, as Save & share will run it: errors, possible participant data (to confirm), warnings. */
function CheckFindings({
  findings,
  confirmed,
  onConfirm,
}: {
  findings: Finding[];
  confirmed: ReadonlySet<string>;
  onConfirm?: (id: string, confirmed: boolean) => void;
}) {
  const data = findings.filter((f) => f.severity === "data");
  const others = findings.filter((f) => f.severity !== "data");
  return (
    <div>
      <p className="dl-label">The check</p>
      {findings.length === 0 ? (
        <p className="mt-1 flex items-baseline gap-1.5 font-sans text-[13px] text-data">
          <Icon name="check" size={13} className="translate-y-[2px]" /> Passes: front matter, links, tables and columns, and
          the participant-data scan.
        </p>
      ) : (
        <FindingList findings={others} />
      )}
      {data.length > 0 && (
        <div className="mt-2">
          <p className="font-sans text-[13px] text-attn">
            These might be participant data (a study ID, a date next to one, a pasted table). They block the save until
            you've checked each one. The scan is best effort: your review is what counts.
          </p>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {data.map((finding) => (
              <li key={finding.id} className="font-sans text-[13px]">
                <label className={clsx("flex items-baseline gap-2", onConfirm ? "cursor-pointer" : "")}>
                  <input
                    type="checkbox"
                    checked={confirmed.has(finding.id)}
                    disabled={!onConfirm}
                    onChange={(e) => onConfirm?.(finding.id, e.target.checked)}
                    className="translate-y-[2px]"
                  />
                  <span>
                    <span className="font-mono text-[12.5px]">
                      {finding.path}
                      {finding.line ? `:${finding.line}` : ""}
                    </span>{" "}
                    {finding.message}{" "}
                    <span className="text-muted">— I've checked: this isn't participant data.</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function FindingList({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {findings.map((finding) => (
        <li key={finding.id} className={clsx("font-sans text-[13px]", finding.severity === "error" ? "text-danger" : finding.severity === "data" ? "text-attn" : "text-muted")}>
          <span className="font-mono text-[12.5px]">
            {finding.path}
            {finding.line ? `:${finding.line}` : ""}
          </span>{" "}
          {finding.severity === "error" ? "Error: " : finding.severity === "warning" ? "Warning: " : ""}
          {finding.message}
        </li>
      ))}
    </ul>
  );
}
