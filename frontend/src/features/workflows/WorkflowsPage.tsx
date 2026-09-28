import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { api } from "@/api/client";
import { pipelinesApi } from "@/api/pipelines";
import { workflowsApi } from "@/api/workflows";
import { type ChatContext, DockedChat } from "@/components/chat/DockedChat";
import { Button, Icon } from "@/components/ui";
import { LoadFailed, LoadingRows } from "@/components/ui/Loading";
import { CHAT_DOCK, chatClass, navClass, useKeptOnceOpen, usePanels } from "@/components/layout/panels";
import { useEscapeToClose } from "@/components/ui/overlay";
import { ACTIONABLE } from "@/features/pipelines/pipelines";
import { useTabState } from "@/features/sql/hooks";

import { Destinations } from "./Delivery";
import { runKey } from "./hooks";
import { NewWorkflow, StagesExplainer } from "./NewWorkflow";
import { PageHeader, type SharedHeader, SharedHeaderContext } from "./PageHeader";
import { RunPage } from "./RunView";
import { WorkflowList } from "./WorkflowList";
import { WorkflowView } from "./WorkflowView";
import { workflowPath } from "./words";

// Workflow authoring (sessions/modes.py): the agent drafts workflow files in its
// copy of the pipelines repo, and its changes become a Pipelines proposal that
// a person reviews and saves there.
export const CHAT_MODE = "workflows";

/** Routines, runs and destinations, with a Workflow authoring chat docked beside them. */
export function WorkflowsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: workflowsApi.list });
  const status = useQuery({ queryKey: ["workflows-status"], queryFn: workflowsApi.status });

  const [chatOpen, setChatOpen] = useTabState<"open" | "closed">(
    "datalab:workflows:chat-open",
    typeof window !== "undefined" && window.matchMedia?.(CHAT_DOCK).matches ? "open" : "closed",
  );
  const panels = usePanels("workflows", { chatOpen: chatOpen === "open", navDefault: 256 });
  const chatKept = useKeptOnceOpen(chatOpen === "open");
  const [drawer, setDrawer] = useState(false);
  // Over the page on a narrow window, the files drawer and the chat close on Escape, as on a click beside them.
  useEscapeToClose(drawer && !panels.navDocked, () => setDrawer(false));
  useEscapeToClose(chatOpen === "open" && !panels.chatDocked, () => setChatOpen("closed"));
  // How many page headers are on screen: each shows "Ask for help" in its action area; with none
  // (a page still loading, say), the page floats its own.
  const [headers, setHeaders] = useState(0);
  const register = useCallback(() => {
    setHeaders((n) => n + 1);
    return () => setHeaders((n) => n - 1);
  }, []);
  const [chatId, setChatId] = useTabState("datalab:workflows:chat", "");
  const [chatKey, setChatKey] = useState(0);
  useForgetMissingChat(chatId, () => setChatId(""));
  const context = useFileContext();
  const queryClient = useQueryClient();

  /** New workflow: a Workflow authoring chat, started with the person's description, shown beside the draft. */
  const startDrafting = async (description: string) => {
    const conversation = await api.createConversation(CHAT_MODE);
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    await api.send(conversation.id, description);
    setChatId(conversation.id);
    setChatKey((k) => k + 1);
    setChatOpen("open");
  };

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

  const askForHelp = (
    <Button data-opens-chat="" variant="secondary" className="shrink-0 px-2.5 py-1 text-[12.5px]" onClick={() => setChatOpen("open")}>
      <Icon name="spark" size={13} /> Ask for help
    </Button>
  );
  const menu = panels.navDocked ? null : (
    <Button variant="ghost" className="-ml-2 shrink-0 px-2" onClick={() => setDrawer(true)} aria-label="Show workflow files">
      <Icon name="menu" size={16} />
    </Button>
  );
  const shared: SharedHeader = { actions: chatOpen === "closed" ? askForHelp : null, menu, register };

  return (
    <div ref={panels.ref} data-panels="workflows" style={panels.style} className="relative grid h-full min-h-0">
      {drawer && !panels.navDocked && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setDrawer(false)} />}
      <aside aria-label="Workflow files" className={clsx(navClass(panels.navDocked, drawer), "overflow-y-auto")}>
        <Rail onNavigate={() => setDrawer(false)} />
      </aside>

      <main className="relative min-h-0 min-w-0 overflow-y-auto">
        {chatOpen === "closed" && headers === 0 && <div className="absolute top-4 right-6 z-10">{askForHelp}</div>}
        <SharedHeaderContext value={shared}>
          <Routes>
            <Route
              index
              element={
                <div className="flex flex-col gap-8 px-6 pt-4 pb-10">
                  <PageHeader
                    actions={
                      workflows.data &&
                      workflows.data.length > 0 && (
                        <Link
                          to="/workflows/new"
                          className="inline-flex shrink-0 items-center gap-1.5 rounded-[3px] bg-accent px-3 py-1.5 font-sans text-[13.5px] font-medium text-accent-ink hover:opacity-85"
                        >
                          <Icon name="spark" size={13} /> New workflow
                        </Link>
                      )
                    }
                  >
                    <h1 className="font-serif text-[23px] leading-tight">Workflows</h1>
                    <p className="mt-0.5 max-w-[46rem] font-sans text-[13px] text-muted">
                      Recipes a person has approved: pull these data, run these steps, check these things, deliver here.
                      DataLab runs them the same way every time, with no AI involved. Runs and their outputs stay on this
                      computer.
                    </p>
                  </PageHeader>
                  {practice && (
                    <p role="note" className="max-w-[46rem] rounded-[3px] bg-sunken px-3 py-2 font-sans text-[13px] text-muted">
                      <strong className="font-medium text-ink">Practice DataLab.</strong> The built-in workflows are the
                      lab's routines, here as examples that run on synthetic data. What you make here is practice-only: it
                      runs on synthetic data and is saved in a practice folder on this computer.
                    </p>
                  )}
                  {workflows.isError && !workflows.data ? (
                    <LoadFailed
                      message={`The workflows couldn't be read: ${workflows.error.message}`}
                      onRetry={() => void workflows.refetch()}
                      retrying={workflows.isFetching}
                    />
                  ) : workflows.data ? (
                    workflows.data.length === 0 ? (
                      <FirstWorkflow message={status.data?.message} />
                    ) : (
                      <WorkflowList workflows={workflows.data} />
                    )
                  ) : (
                    // Never the empty "Make your first workflow" before the list is in.
                    <LoadingRows label="Loading workflows…" />
                  )}
                  <Destinations practice={practice} />
                </div>
              }
            />
            <Route
              path="new"
              element={
                <NewWorkflow
                  status={status.data}
                  chatId={chatId}
                  onStart={startDrafting}
                  onShowChat={() => setChatOpen("open")}
                  onNewChat={() => {
                    setChatId("");
                    setChatKey((k) => k + 1);
                  }}
                />
              }
            />
            <Route path="file" element={<FileRoute />} />
            <Route path="runs/:runId" element={<RunRoute />} />
            <Route path="*" element={<p className="px-6 py-6 font-sans text-[13px] text-muted">Nothing here.</p>} />
          </Routes>
        </SharedHeaderContext>
      </main>

      {chatOpen === "open" && !panels.chatDocked && (
        <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setChatOpen("closed")} />
      )}
      {/* Kept mounted while hidden, so closing and reopening it keeps its draft and place. */}
      {chatKept && (
        <aside aria-label="Workflow authoring chat" hidden={chatOpen !== "open"} className={chatClass(panels.chatDocked)}>
          {chatId && <WaitingInPipelines conversationId={chatId} />}
          <DockedChat
            key={chatKey}
            mode={CHAT_MODE}
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

/** No workflows yet: making one is the page's main action. */
function FirstWorkflow({ message }: { message?: string | null }) {
  return (
    <section
      aria-labelledby="first-workflow"
      className="mx-auto flex w-full max-w-[44rem] flex-col items-center gap-5 rounded-[4px] border border-line bg-surface px-6 py-10 text-center"
    >
      <h2 id="first-workflow" className="font-serif text-[28px] leading-tight">
        Make your first workflow
      </h2>
      <p className="max-w-[34rem] font-sans text-[13.5px] text-muted">
        Describe a task you do again and again. The assistant asks about the details and drafts it; you review each
        stage, test it, and save it to run whenever you need it.
      </p>
      <Link
        to="/workflows/new"
        className="inline-flex items-center gap-1.5 rounded-[3px] bg-accent px-4 py-2 font-sans text-[14px] font-medium text-accent-ink hover:opacity-85"
      >
        <Icon name="spark" size={14} /> New workflow
      </Link>
      <div className="w-full border-t border-line pt-5 text-left">
        <StagesExplainer />
      </div>
      {message && <p className="font-sans text-[12.5px] text-faint">{message}</p>}
    </section>
  );
}

/** The chat's changes are a Pipelines proposal: reviewed, tested and saved there, never here. */
function WaitingInPipelines({ conversationId }: { conversationId: string }) {
  const navigate = useNavigate();
  const [, setProposalId] = useTabState("datalab:pipelines:proposal", "");
  const proposals = useQuery({
    queryKey: ["pipeline-proposals", conversationId],
    queryFn: () => pipelinesApi.proposals(conversationId),
    // A turn's proposal is made after the turn: looked for again now and then.
    refetchInterval: 10_000,
    retry: false,
  });
  const waiting = proposals.data?.find((p) => p.conversation_id === conversationId && ACTIONABLE.has(p.status));
  if (!waiting) return null;
  return (
    <p role="status" className="flex items-center gap-2 border-b border-line bg-accent-soft px-4 py-2 font-sans text-[12.5px]">
      <span className="min-w-0 flex-1">This chat's change is waiting for review in Pipelines.</span>
      <Button
        variant="secondary"
        className="shrink-0 px-2 py-0.5 text-[12px]"
        onClick={() => {
          setProposalId(waiting.id);
          navigate("/pipelines");
        }}
      >
        Review it
      </Button>
    </p>
  );
}

function FileRoute() {
  const [params] = useSearchParams();
  const path = params.get("path") ?? "";
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: workflowsApi.list });
  if (workflows.isError) return <p className="px-6 py-6 font-sans text-[13px] text-danger">{workflows.error.message}</p>;
  if (!workflows.data) return <p className="px-6 py-6 font-sans text-[13px] text-muted">Reading the workflow…</p>;
  const workflow = workflows.data.find((w) => w.path === path);
  if (!workflow) {
    return (
      <div className="px-6 py-6 font-sans text-[13px] text-muted">
        <p>There's no workflow file {path ? <span className="font-mono">{path}</span> : ""} in the folder any more.</p>
        <Link to="/workflows" className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
          Back to the workflows
        </Link>
      </div>
    );
  }
  return <WorkflowView key={workflow.path} workflow={workflow} />;
}

function RunRoute() {
  const { runId = "" } = useParams();
  return <RunPage key={runId} runId={runId} />;
}

/** The workflow files, always at hand beside the page. */
function Rail({ onNavigate }: { onNavigate: () => void }) {
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: workflowsApi.list });
  const location = useLocation();
  const [params] = useSearchParams();
  const current = location.pathname.endsWith("/workflows/file") ? params.get("path") : null;
  return (
    <nav className="flex flex-col py-3">
      <Link to="/workflows" onClick={onNavigate} className="dl-label px-4 py-1.5 hover:text-ink">
        Workflows
      </Link>
      {workflows.isPending && <LoadingRows label="Loading…" rows={3} dense quiet className="px-4 py-1.5" />}
      {workflows.data?.map((w) => (
        <Link
          key={w.path}
          to={workflowPath(w.path)}
          onClick={onNavigate}
          aria-current={current === w.path ? "page" : undefined}
          className={clsx(
            "flex items-center gap-2 px-4 py-1.5 font-sans text-[13px]",
            current === w.path ? "bg-accent-soft font-medium text-ink" : "text-muted hover:text-ink",
          )}
        >
          <span aria-hidden className={clsx("size-[6px] shrink-0 rounded-full", w.valid ? "bg-data" : "bg-danger")} />
          <span className="min-w-0 truncate">{w.name ?? w.path}</span>
          <span className="sr-only">{w.valid ? "(ready to run)" : "(has problems)"}</span>
        </Link>
      ))}
    </nav>
  );
}

/** The workflow file the page is about, to send with a message if the person ticks the box. Definitions only:
 *  never a run's outputs or data. */
function useFileContext(): ChatContext | undefined {
  const location = useLocation();
  const [params] = useSearchParams();
  const runMatch = /\/workflows\/runs\/([^/]+)$/.exec(location.pathname);
  const runId = runMatch ? decodeURIComponent(runMatch[1]) : "";
  // The run page's own query: only its workflow's path is used here.
  const run = useQuery({ queryKey: runKey(runId), queryFn: () => workflowsApi.run(runId), enabled: Boolean(runId) });
  const onFile = location.pathname.endsWith("/workflows/file");
  const path = onFile ? params.get("path") : runId ? run.data?.workflow_path : null;
  const text = useQuery({
    queryKey: ["workflow-text", path ?? ""],
    queryFn: () => workflowsApi.text(path!),
    enabled: Boolean(path),
  });
  return useMemo(
    () => (path && text.data ? { label: `The workflow file ${path}`, text: text.data.text, language: "yaml" } : undefined),
    [path, text.data],
  );
}

/** Forget the docked chat's conversation if it's gone from DataLab (deleted in the Workspace)
 *  by the time this page opens. A chat started here is never forgotten this way. */
function useForgetMissingChat(chatId: string, forget: () => void) {
  const initial = useRef(chatId).current;
  const checking = Boolean(initial) && chatId === initial;
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations, enabled: checking });
  const missing = checking && conversations.isSuccess && !conversations.data.some((c) => c.id === chatId);
  useEffect(() => {
    if (missing) forget();
  }, [missing, forget]);
}
