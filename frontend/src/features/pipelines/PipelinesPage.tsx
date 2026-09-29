import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/api/client";
import { type PipelineEditSummary, type PipelineProposal, pipelinesApi } from "@/api/pipelines";
import { REAL_ONLY } from "@/components/chat/KbSuggestionCard";
import { type ChatContext, DockedChat } from "@/components/chat/DockedChat";
import { CodeEditor } from "@/components/editor/CodeEditor";
import { Button, Chip, Icon, Tabs } from "@/components/ui";
import { LoadFailed, LoadingRows } from "@/components/ui/Loading";
import { CHAT_DOCK, chatClass, messageBox, navClass, useKeptOnceOpen, usePanels } from "@/components/layout/panels";
import { useOverlay } from "@/components/ui/overlay";
import { formatBytes } from "@/lib/csv";
import { useTabState } from "@/features/sql/hooks";

import { EditView, type EditTarget, forgetUnsaved } from "./EditView";
import { FileTree } from "./FileTree";
import { ACTIONABLE, EDIT_OPEN, editChip, editTitle, languageOf, proposalChip, repoLine, when } from "./pipelines";
import { ProposalView } from "./ProposalView";

// The Pipelines tab's own chat (backend sessions/modes.py): Data engineering's rules, with
// the file open here; its changes come back as a proposal to review in this tab.
export const CHAT_MODE = "pipelines";

/** What the editor has open, as kept in the tab's state: "id:<edit>", "file:<path>" or "new:<path>". */
function editTarget(value: string): EditTarget | null {
  if (value.startsWith("id:")) return { id: value.slice(3) };
  if (value.startsWith("file:")) return { path: value.slice(5) };
  if (value.startsWith("new:")) return { path: value.slice(4), isNew: true };
  return null;
}

/** The ihsDataR code browser, changes to review (the assistant's, and the person's own edits), and tests, with the Pipelines assistant docked beside them. */
export function PipelinesPage() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["pipelines-status"], queryFn: pipelinesApi.status });
  const ready = Boolean(status.data?.head) && status.data?.repo !== "signed out";
  const tree = useQuery({ queryKey: ["pipelines-files", status.data?.head], queryFn: pipelinesApi.files, enabled: ready });
  // Proposals appear after the agent's turns, from any Data engineering or Workflow
  // authoring conversation (the Workflows tab's chat).
  const proposals = useQuery({
    queryKey: ["pipeline-proposals"],
    queryFn: () => pipelinesApi.proposals(),
    enabled: Boolean(status.data?.available),
    refetchInterval: 5000,
  });
  // The person's own edits: drafts on this computer, and the latest shared or discarded.
  const edits = useQuery({
    queryKey: ["pipeline-edits"],
    queryFn: pipelinesApi.edits,
    enabled: Boolean(status.data?.available),
    refetchInterval: 5000,
  });
  // Edits finished elsewhere (another window, another browser): their unkept typing here is stale.
  useEffect(() => {
    for (const e of edits.data ?? []) if (e.status === "saved" || e.status === "discarded") forgetUnsaved(e.id);
  }, [edits.data]);
  const sync = useMutation({
    mutationFn: pipelinesApi.sync,
    onSuccess: (next) => {
      queryClient.setQueryData(["pipelines-status"], next);
    },
  });

  const [side, setSide] = useTabState<"files" | "changes">("datalab:pipelines:side", "files");
  const [drawer, setDrawer] = useState(false);
  const [path, setPath] = useTabState("datalab:pipelines:file", "");
  const [proposalId, setProposalId] = useTabState("datalab:pipelines:proposal", "");
  const [editing, setEditing] = useTabState("datalab:pipelines:edit", "");
  const target = editTarget(editing);
  const [creating, setCreating] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useTabState<"open" | "closed">(
    "datalab:pipelines:chat-open",
    typeof window !== "undefined" && window.matchMedia?.(CHAT_DOCK).matches ? "open" : "closed",
  );
  const panels = usePanels("pipelines", { chatOpen: chatOpen === "open" });
  const chatKept = useKeptOnceOpen(chatOpen === "open");
  const [chatId, setChatId] = useTabState("datalab:pipelines:chat", "");
  const [chatKey, setChatKey] = useState(0);
  useForgetMissingChat(chatId, () => setChatId(""));
  // Below these widths each is a drawer over the page.
  const drawerBox = useRef<HTMLElement>(null);
  const chatBox = useRef<HTMLElement>(null);
  // Docked again (the window widened), the list is no longer a drawer to close.
  if (drawer && panels.navDocked) setDrawer(false);
  const drawerOver = drawer && !panels.navDocked;
  const chatOver = chatOpen === "open" && !panels.chatDocked;
  // Over the page, each is a dialog: focus in it and kept there, Escape closes it, focus goes back.
  useOverlay(drawerBox, drawerOver, () => setDrawer(false));
  // The button that opened the chat is gone while it's open: its successor gets focus back.
  useOverlay(chatBox, chatOver, () => setChatOpen("closed"), askButton, messageBox);

  const file = useQuery({
    queryKey: ["pipelines-file", status.data?.head, path],
    queryFn: () => pipelinesApi.file(path),
    enabled: ready && Boolean(path),
  });
  // The file being read, for the person to send along with a message if they choose.
  const context = useMemo<ChatContext | undefined>(
    () =>
      file.data?.text != null && !proposalId && !editing
        ? { label: `${path}, as on main`, name: path, text: file.data.text, language: languageOf(path) }
        : undefined,
    [file.data, path, proposalId, editing],
  );
  const open = (next: string) => {
    setPath(next);
    setProposalId("");
    setEditing("");
    setDrawer(false);
  };
  const review = (id: string) => {
    setProposalId(id);
    setEditing("");
    setDrawer(false);
  };
  const openEdit = (id: string) => {
    setEditing(`id:${id}`);
    setProposalId("");
    setDrawer(false);
  };
  const waiting =
    (proposals.data ?? []).filter((p) => ACTIONABLE.has(p.status)).length +
    (edits.data ?? []).filter((e) => EDIT_OPEN.has(e.status)).length;
  const signedIn = Boolean(status.data?.signed_in);
  const line = repoLine(status.data);

  const sidePanel = (
    <div className="flex h-full min-h-0 flex-col">
      <Tabs
        tabs={[
          { id: "files", label: "Files" },
          { id: "changes", label: waiting ? `Changes (${waiting})` : "Changes" },
        ]}
        value={side}
        onChange={setSide}
      />
      {side === "files" ? (
        tree.data && tree.data.files.length > 0 ? (
          <FileTree files={tree.data.files} selected={proposalId || editing ? null : path} onOpen={open} />
        ) : status.isError ? (
          <div className="px-4 py-4">
            <LoadFailed message="The pipelines repo's state couldn't be read." onRetry={() => void status.refetch()} retrying={status.isFetching} />
          </div>
        ) : status.isPending || (ready && tree.isPending) ? (
          <LoadingRows label="Loading the files…" rows={5} dense className="px-4 py-4" />
        ) : tree.isError ? (
          <div className="px-4 py-4">
            <LoadFailed message="The files couldn't be read." onRetry={() => void tree.refetch()} retrying={tree.isFetching} />
          </div>
        ) : (
          <p className="px-4 py-4 font-sans text-[13px] text-muted">
            The repo's files appear here once it's synced.
          </p>
        )
      ) : (
        proposals.data || (status.data && !status.data.available) ? (
          <ProposalList
            proposals={proposals.data ?? []}
            edits={edits.data ?? []}
            chatId={chatId}
            selected={target && "id" in target ? target.id : proposalId}
            onOpen={review}
            onOpenEdit={openEdit}
          />
        ) : status.isError || proposals.isError ? (
          <div className="px-4 py-4">
            <LoadFailed
              message="The changes couldn't be read."
              onRetry={() => void (status.isError ? status.refetch() : proposals.refetch())}
              retrying={status.isFetching || proposals.isFetching}
            />
          </div>
        ) : (
          <LoadingRows label="Loading the changes…" rows={3} dense className="px-4 py-4" />
        )
      )}
    </div>
  );

  const chatActions = (
    <>
      <Button
        variant="ghost"
        className="px-2 text-[12.5px]"
        onClick={() => {
          setChatId("");
          setChatKey((k) => k + 1);
        }}
      >
        New chat
      </Button>
      <Button variant="ghost" className="px-2" aria-label="Hide the chat" onClick={() => setChatOpen("closed")}>
        <Icon name="close" size={14} />
      </Button>
    </>
  );

  return (
    <div ref={panels.ref} data-panels="pipelines" style={panels.style} className="relative grid h-full min-h-0">
      {drawerOver && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setDrawer(false)} />}
      <aside
        ref={drawerBox}
        id={panels.navId}
        aria-label="Files and changes"
        role={drawerOver ? "dialog" : undefined}
        aria-modal={drawerOver || undefined}
        tabIndex={-1}
        className={navClass(panels.navDocked, drawer)}
      >
        {sidePanel}
      </aside>

      <main className="flex min-h-0 min-w-0 flex-col">
        <header className="flex items-start gap-3 px-5 pt-4 pb-3">
          {!panels.navDocked && (
            <Button variant="ghost" className="-ml-2 px-2" onClick={() => setDrawer(true)} aria-label="Show files and changes">
              <Icon name="menu" size={16} />
            </Button>
          )}
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-[23px] leading-tight">Pipelines</h1>
            <p className="mt-0.5 max-w-[46rem] font-sans text-[13px] text-muted">
              The lab's ihsDataR package and workflow files. Ask the Pipelines assistant to explain or change them:
              it works on its own copy, and what it changes comes here to review, test, and save.
            </p>
            <p
              role="status"
              className={clsx(
                "mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-sans text-[12.5px]",
                line.tone === "attn" && "text-attn",
                line.tone === "bad" && "text-danger",
                line.tone === "muted" && "text-muted",
              )}
            >
              {status.data?.name && <span className="font-mono text-[12px] text-ink">{status.data.name}</span>}
              <span>{line.text}</span>
              {status.data?.available && status.data.repo !== "signed out" && (
                <Button variant="ghost" className="px-1 py-0 text-[12.5px]" onClick={() => sync.mutate()} disabled={sync.isPending}>
                  {sync.isPending ? "Syncing…" : "Sync"}
                </Button>
              )}
              {ready && (
                <Button
                  variant="ghost"
                  className="px-1 py-0 text-[12.5px]"
                  aria-expanded={creating !== null}
                  onClick={() => setCreating(creating === null ? folderOf(path) : null)}
                >
                  <Icon name="file" size={12} /> New file
                </Button>
              )}
            </p>
            {creating !== null && (
              <NewFile
                value={creating}
                onChange={setCreating}
                onCancel={() => setCreating(null)}
                onCreate={(p) => {
                  setCreating(null);
                  setEditing(`new:${p}`);
                  setProposalId("");
                }}
              />
            )}
          </div>
          {chatOpen === "closed" && (
            <Button
              data-opens-chat=""
              variant="secondary"
              className="shrink-0 px-2.5 py-1 text-[12.5px]"
              onClick={() => setChatOpen("open")}
            >
              <Icon name="spark" size={13} /> Ask the agent
            </Button>
          )}
        </header>

        <div className="flex min-h-0 flex-1 flex-col border-t border-line">
          {target ? (
            <EditView
              key={editing}
              target={target}
              repo={status.data?.name}
              signedIn={signedIn}
              onLoaded={(id) => setEditing(`id:${id}`)}
              onClose={() => setEditing("")}
              onOpenProposal={review}
            />
          ) : proposalId ? (
            <ProposalView key={proposalId} id={proposalId} onBack={() => setProposalId("")} onEdit={openEdit} />
          ) : path && ready ? (
            <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-5">
              <p className="mb-2 flex flex-wrap items-baseline gap-x-3 font-mono text-[12.5px]">
                <span className="text-ink">{path}</span>
                {file.data && (
                  <span className="font-sans text-muted">
                    {formatBytes(file.data.size)}, at <span className="font-mono">{file.data.head.slice(0, 7)}</span> on
                    main.
                  </span>
                )}
                {file.data?.editable && (
                  <Button
                    variant="secondary"
                    className="ml-auto px-2.5 py-1 font-sans text-[12.5px]"
                    onClick={() => setEditing(file.data!.draft ? `id:${file.data!.draft}` : `file:${path}`)}
                  >
                    <Icon name="pen" size={12} /> {file.data.draft ? "Continue editing" : "Edit manually"}
                  </Button>
                )}
              </p>
              {file.data && !file.data.editable && file.data.text != null && (
                <p className="mb-2 font-sans text-[12.5px] text-muted">
                  Read-only here{file.data.source_note ? `: ${file.data.source_note}` : "."}
                </p>
              )}
              {file.data?.draft && (
                <p className="mb-2 font-sans text-[12.5px] text-attn">
                  You have a draft of this file on this computer (not shared). This is the version on main.
                </p>
              )}
              {file.data?.text != null ? (
                <CodeEditor
                  label={path}
                  language={languageOf(path)}
                  value={file.data.text}
                  readOnly
                  className="min-h-0 flex-1"
                />
              ) : (
                <p className="font-sans text-[13px] text-muted">
                  {file.isError
                    ? "This file couldn't be read."
                    : file.data
                      ? file.data.too_large
                        ? "This file is too large to show here."
                        : "This isn't a text file, so it isn't shown."
                      : "Loading…"}
                </p>
              )}
            </div>
          ) : (
            <Overview
              proposals={proposals.data ?? []}
              edits={edits.data ?? []}
              ready={ready}
              practice={Boolean(status.data?.practice)}
              loading={status.isPending}
              onReview={review}
              onOpenEdit={openEdit}
            />
          )}
        </div>
      </main>

      {chatOver && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setChatOpen("closed")} />}
      {/* Kept mounted while hidden, so closing and reopening it keeps its draft and place. */}
      {chatKept && (
        <aside
          ref={chatBox}
          id={panels.chatId}
          aria-label="Pipelines assistant"
          hidden={chatOpen !== "open"}
          role={chatOver ? "dialog" : undefined}
          aria-modal={chatOver || undefined}
          tabIndex={-1}
          className={chatClass(panels.chatDocked)}
        >
          <DockedChat
            key={chatKey}
            mode={CHAT_MODE}
            assistant="pipelines"
            conversationId={chatId || undefined}
            onConversation={(conversation) => setChatId(conversation.id)}
            context={context}
            headerActions={chatActions}
            active={chatOpen === "open"}
          />
        </aside>
      )}
      {panels.dividers}
    </div>
  );
}

function ProposalList({
  proposals,
  edits,
  chatId,
  selected,
  onOpen,
  onOpenEdit,
}: {
  proposals: PipelineProposal[];
  edits: PipelineEditSummary[];
  chatId: string;
  selected: string;
  onOpen: (id: string) => void;
  onOpenEdit: (id: string) => void;
}) {
  if (proposals.length === 0 && edits.length === 0) {
    return (
      <p className="px-4 py-4 font-sans text-[13px] leading-relaxed text-muted">
        Nothing proposed yet. When the Pipelines or Workflow assistant (or a Data engineering conversation) changes
        ihsDataR or a workflow file, the change appears here after its turn. Your own edits (Edit manually on a file)
        appear here too.
      </p>
    );
  }
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
      {edits.map((e) => {
        const chip = editChip(e);
        return (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => onOpenEdit(e.id)}
              aria-current={e.id === selected ? "true" : undefined}
              className={clsx(
                "flex w-full flex-col gap-1 rounded-[3px] px-2.5 py-2 text-left",
                e.id === selected ? "bg-surface shadow-[inset_0_0_0_1px_var(--color-line)]" : "hover:bg-surface/70",
              )}
            >
              <span className="flex items-center gap-2">
                <Chip tone={chip.tone}>{chip.text}</Chip>
              </span>
              <span className="line-clamp-2 font-sans text-[13px] text-ink">{editTitle(e.paths)}</span>
              <span className="font-sans text-[11.5px] text-muted">
                Your edit{e.from_proposal ? ", of the assistant's suggestion" : ""} · {when(e.updated_at)}
              </span>
            </button>
          </li>
        );
      })}
      {proposals.map((p) => {
        const chip = proposalChip(p);
        return (
          <li key={p.id}>
            <button
              type="button"
              onClick={() => onOpen(p.id)}
              aria-current={p.id === selected ? "true" : undefined}
              className={clsx(
                "flex w-full flex-col gap-1 rounded-[3px] px-2.5 py-2 text-left",
                p.id === selected ? "bg-surface shadow-[inset_0_0_0_1px_var(--color-line)]" : "hover:bg-surface/70",
              )}
            >
              <span className="flex items-center gap-2">
                <Chip tone={chip.tone}>{chip.text}</Chip>
                {p.conversation_id === chatId && <span className="font-sans text-[11.5px] text-muted">this chat</span>}
              </span>
              <span className="line-clamp-2 font-sans text-[13px] text-ink">
                {p.files.length === 1 ? p.files[0].path : `${p.files.length} files`}
              </span>
              <span className="font-sans text-[11.5px] text-muted">
                Suggested by the assistant in {p.conversation_title ?? "a conversation"} · {when(p.created_at)}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** What the middle shows before a file or a change is opened. */
function Overview({
  proposals,
  edits,
  ready,
  practice,
  loading,
  onReview,
  onOpenEdit,
}: {
  proposals: PipelineProposal[];
  edits: PipelineEditSummary[];
  ready: boolean;
  /** Practice DataLab: no lab repos, so nothing to edit. */
  practice: boolean;
  /** The repo's state isn't known yet: nothing said about syncing until it is. */
  loading: boolean;
  onReview: (id: string) => void;
  onOpenEdit: (id: string) => void;
}) {
  const waiting = proposals.filter((p) => ACTIONABLE.has(p.status));
  const drafts = edits.filter((e) => EDIT_OPEN.has(e.status));
  return (
    <div className="max-w-[40rem] px-5 py-6 font-sans text-[13.5px] leading-relaxed text-muted">
      {drafts.length > 0 && (
        <div className="mb-4">
          <p className="text-ink">
            {drafts.length} edit{drafts.length === 1 ? "" : "s"} of yours, kept on this computer:
          </p>
          <ul className="mt-2 flex flex-col gap-1">
            {drafts.map((e) => (
              <li key={e.id}>
                <button
                  type="button"
                  onClick={() => onOpenEdit(e.id)}
                  className="text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
                >
                  {editTitle(e.paths)}
                </button>{" "}
                ({editChip(e).text})
              </li>
            ))}
          </ul>
        </div>
      )}
      {practice ? (
        <div className="flex flex-col items-start gap-2">
          <p>Practice DataLab has no pipelines repo: the lab's ihsDataR package and workflow files are on the real DataLab.</p>
          <Button disabled title={REAL_ONLY}>
            <Icon name="pen" size={13} /> Edit manually
          </Button>
          <p className="text-[12.5px]">{REAL_ONLY}.</p>
        </div>
      ) : waiting.length > 0 ? (
        <>
          <p className="text-ink">
            {waiting.length} change{waiting.length === 1 ? "" : "s"} to review.
          </p>
          <ul className="mt-2 flex flex-col gap-1">
            {waiting.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => onReview(p.id)}
                  className="text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
                >
                  {p.files.length === 1 ? p.files[0].path : `${p.files.length} files`}
                </button>{" "}
                from “{p.conversation_title ?? "a conversation"}”
              </li>
            ))}
          </ul>
        </>
      ) : loading ? (
        <LoadingRows label="Loading the pipelines repo…" rows={2} dense />
      ) : ready ? (
        <p>Open a file on the left to read it and edit it manually, or ask the agent for a change.</p>
      ) : (
        <p>The package's files appear here once the pipelines repo is synced.</p>
      )}
    </div>
  );
}

/** The folder of the file open, for a new file beside it (the package's R code if none). */
function folderOf(path: string): string {
  const at = path.lastIndexOf("/");
  return at > 0 ? path.slice(0, at + 1) : "ihsDataR/R/";
}

/** Where a new file goes, and its name: a text file in ihsDataR/ or a workflow file. */
function NewFile({
  value,
  onChange,
  onCancel,
  onCreate,
}: {
  value: string;
  onChange: (value: string) => void;
  onCancel: () => void;
  onCreate: (path: string) => void;
}) {
  const path = value.trim();
  const named = path.length > 0 && !path.endsWith("/");
  return (
    <form
      className="mt-2 flex flex-wrap items-center gap-2 font-sans text-[12.5px]"
      onSubmit={(e) => {
        e.preventDefault();
        if (named) onCreate(path);
      }}
    >
      <label className="flex min-w-0 flex-1 items-center gap-2">
        <span className="text-muted">New file</span>
        <input
          autoFocus
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="ihsDataR/R/new_function.R"
          className="min-w-[16rem] flex-1 rounded-[3px] border border-line bg-field px-2 py-1 font-mono text-[12.5px] outline-none focus:border-ink"
        />
      </label>
      <Button type="submit" variant="primary" className="px-2.5 py-1 text-[12.5px]" disabled={!named}>
        Create
      </Button>
      <Button type="button" variant="ghost" className="px-2 py-1 text-[12.5px]" onClick={onCancel}>
        Cancel
      </Button>
      <p className="w-full text-muted">
        In ihsDataR/ (R code, tests, pipelines) or workflows/&lt;name&gt;.yaml. It stays a draft on this computer until you
        Save &amp; share it.
      </p>
    </form>
  );
}

const askButton = () => document.querySelector<HTMLElement>("[data-opens-chat]");

/** A chat kept from before that isn't in DataLab any more (deleted): start afresh. */
function useForgetMissingChat(chatId: string, forget: () => void) {
  const initial = useRef(chatId).current;
  const checking = Boolean(initial) && chatId === initial;
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations, enabled: checking });
  const missing = checking && conversations.isSuccess && !conversations.data.some((c) => c.id === chatId);
  useEffect(() => {
    if (missing) forget();
  }, [missing, forget]);
}
