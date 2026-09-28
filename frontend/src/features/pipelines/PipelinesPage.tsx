import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type RefObject, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/api/client";
import { type PipelineProposal, pipelinesApi } from "@/api/pipelines";
import { type ChatContext, DockedChat } from "@/components/chat/DockedChat";
import { CodeEditor } from "@/components/editor/CodeEditor";
import { Button, Chip, Icon, Tabs } from "@/components/ui";
import { LoadFailed, LoadingRows } from "@/components/ui/Loading";
import { CHAT_DOCK, chatClass, NAV_DOCK, navClass, useKeptOnceOpen, usePanels } from "@/components/layout/panels";
import { formatBytes } from "@/lib/csv";
import { useTabState } from "@/features/sql/hooks";

import { FileTree } from "./FileTree";
import { ACTIONABLE, languageOf, proposalChip, repoLine, when } from "./pipelines";
import { ProposalView } from "./ProposalView";


/** The ihsDataR code browser, agent-proposed changes to review, and tests, with a Data engineering chat docked beside them. */
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
  useOverlay(drawer, () => setDrawer(false), drawerBox, NAV_DOCK);
  // The button that opened the chat is gone while it's open: its successor gets focus back.
  useOverlay(chatOpen === "open", () => setChatOpen("closed"), chatBox, CHAT_DOCK, askButton);

  const file = useQuery({
    queryKey: ["pipelines-file", status.data?.head, path],
    queryFn: () => pipelinesApi.file(path),
    enabled: ready && Boolean(path),
  });
  // The file being read, for the person to send along with a message if they choose.
  const context = useMemo<ChatContext | undefined>(
    () =>
      file.data?.text != null && !proposalId
        ? { label: `${path}, as on main`, text: file.data.text, language: languageOf(path) }
        : undefined,
    [file.data, path, proposalId],
  );
  const open = (next: string) => {
    setPath(next);
    setProposalId("");
    setDrawer(false);
  };
  const review = (id: string) => {
    setProposalId(id);
    setDrawer(false);
  };
  const waiting = (proposals.data ?? []).filter((p) => ACTIONABLE.has(p.status)).length;
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
          <FileTree files={tree.data.files} selected={proposalId ? null : path} onOpen={open} />
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
        proposals.data || !status.data?.available ? (
          <ProposalList proposals={proposals.data ?? []} chatId={chatId} selected={proposalId} onOpen={review} />
        ) : proposals.isError ? (
          <div className="px-4 py-4">
            <LoadFailed message="The changes couldn't be read." onRetry={() => void proposals.refetch()} retrying={proposals.isFetching} />
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
      {drawer && !panels.navDocked && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setDrawer(false)} />}
      <aside ref={drawerBox} aria-label="Files and changes" className={navClass(panels.navDocked, drawer)}>
        {sidePanel}
      </aside>

      <main className="flex min-h-0 min-w-0 flex-col">
        <header className="flex items-start gap-3 px-5 pt-4 pb-3">
          <Button variant="ghost" className={clsx("-ml-2 px-2", panels.navDocked && "hidden")} onClick={() => setDrawer(true)} aria-label="Show files and changes">
            <Icon name="menu" size={16} />
          </Button>
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-[23px] leading-tight">Pipelines</h1>
            <p className="mt-0.5 max-w-[46rem] font-sans text-[13px] text-muted">
              The lab's ihsDataR package and workflow files. Ask the Data engineering agent for a change: it works on
              its own copy, and what it changes comes here to review, test, and save.
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
            </p>
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
          {proposalId ? (
            <ProposalView key={proposalId} id={proposalId} onBack={() => setProposalId("")} />
          ) : path && ready ? (
            <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-5">
              <p className="mb-2 flex flex-wrap items-baseline gap-x-3 font-mono text-[12.5px]">
                <span className="text-ink">{path}</span>
                {file.data && (
                  <span className="font-sans text-muted">
                    {formatBytes(file.data.size)}, at <span className="font-mono">{file.data.head.slice(0, 7)}</span> on
                    main. Read-only: ask the agent to change it.
                  </span>
                )}
              </p>
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
            <Overview proposals={proposals.data ?? []} ready={ready} loading={status.isPending} onReview={review} />
          )}
        </div>
      </main>

      {chatOpen === "open" && !panels.chatDocked && (
        <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setChatOpen("closed")} />
      )}
      {/* Kept mounted while hidden, so closing and reopening it keeps its draft and place. */}
      {chatKept && (
        <aside ref={chatBox} aria-label="Data engineering chat" hidden={chatOpen !== "open"} className={chatClass(panels.chatDocked)}>
          <DockedChat
            key={chatKey}
            mode="engineering"
            conversationId={chatId || undefined}
            onConversation={(conversation) => setChatId(conversation.id)}
            context={context}
            headerActions={chatActions}
          />
        </aside>
      )}
      {panels.dividers}
    </div>
  );
}

function ProposalList({
  proposals,
  chatId,
  selected,
  onOpen,
}: {
  proposals: PipelineProposal[];
  chatId: string;
  selected: string;
  onOpen: (id: string) => void;
}) {
  if (proposals.length === 0) {
    return (
      <p className="px-4 py-4 font-sans text-[13px] leading-relaxed text-muted">
        Nothing proposed yet. When the Data engineering or Workflow authoring agent changes ihsDataR or a workflow
        file, the change appears here after its turn.
      </p>
    );
  }
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
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
                {p.conversation_title ?? "A conversation"} · {when(p.created_at)}
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
  ready,
  loading,
  onReview,
}: {
  proposals: PipelineProposal[];
  ready: boolean;
  /** The repo's state isn't known yet: nothing said about syncing until it is. */
  loading: boolean;
  onReview: (id: string) => void;
}) {
  const waiting = proposals.filter((p) => ACTIONABLE.has(p.status));
  return (
    <div className="max-w-[40rem] px-5 py-6 font-sans text-[13.5px] leading-relaxed text-muted">
      {waiting.length > 0 ? (
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
        <p>Open a file on the left to read it, or ask the agent for a change.</p>
      ) : (
        <p>The package's files appear here once the pipelines repo is synced.</p>
      )}
    </div>
  );
}

/**
 * A drawer over the page, below `wide`: focus goes into it as it opens and back
 * to what opened it as it closes, and Escape closes it (unless a dialog, such
 * as a number's sources, is open: that closes first).
 */
function useOverlay(
  open: boolean,
  close: () => void,
  box: RefObject<HTMLElement | null>,
  wide: string,
  // Where focus goes back to if what opened it isn't there any more.
  returnTo?: () => HTMLElement | null,
) {
  const closing = useRef(close);
  closing.current = close;
  useEffect(() => {
    if (!open || window.matchMedia?.(wide).matches) return;
    // (Not the page itself, when the button that opened it is already gone.)
    const active = document.activeElement;
    const opener = active instanceof HTMLElement && active !== document.body ? active : null;
    if (!box.current?.contains(document.activeElement)) {
      box.current?.querySelector<HTMLElement>("textarea, input, button, a[href]")?.focus();
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("[role=dialog]")) closing.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener?.isConnected) opener.focus();
      else returnTo?.()?.focus();
    };
  }, [open, box, wide, returnTo]);
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
