import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Route, Routes, useLocation, useParams, useSearchParams } from "react-router";

import { api } from "@/api/client";
import { workflowsApi } from "@/api/workflows";
import { type ChatContext, DockedChat } from "@/components/chat/DockedChat";
import { Button, Icon } from "@/components/ui";
import { useTabState } from "@/features/sql/hooks";

import { Destinations } from "./Delivery";
import { runKey } from "./hooks";
import { RunPage } from "./RunView";
import { WorkflowList } from "./WorkflowList";
import { WorkflowView } from "./WorkflowView";
import { workflowPath } from "./words";

const WIDE = "(min-width: 1280px)";

// DataLab has no "Workflow authoring" mode yet (sessions/modes.py). Until it
// does, the docked chat opens in Data engineering, the mode for the lab's
// pipelines repo, where workflow files live beside the code they call.
export const CHAT_MODE = "engineering";

/** Routines, runs and destinations, with a Workflow authoring chat docked beside them. */
export function WorkflowsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: workflowsApi.list });
  const status = useQuery({ queryKey: ["workflows-status"], queryFn: workflowsApi.status });

  const [chatOpen, setChatOpen] = useTabState<"open" | "closed">(
    "datalab:workflows:chat-open",
    typeof window !== "undefined" && window.matchMedia?.(WIDE).matches ? "open" : "closed",
  );
  const [chatId, setChatId] = useTabState("datalab:workflows:chat", "");
  const [chatKey, setChatKey] = useState(0);
  useForgetMissingChat(chatId, () => setChatId(""));
  const context = useFileContext();

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
    <div
      className={clsx(
        "relative grid h-full min-h-0 grid-cols-[minmax(0,1fr)] lg:grid-cols-[16rem_minmax(0,1fr)]",
        chatOpen === "open" && "xl:grid-cols-[16rem_minmax(0,1fr)_26rem] 2xl:grid-cols-[17rem_minmax(0,1fr)_30rem]",
      )}
    >
      <aside aria-label="Workflow files" className="hidden min-h-0 overflow-y-auto border-r border-line bg-rail lg:block">
        <Rail />
      </aside>

      <main className="relative min-h-0 min-w-0 overflow-y-auto">
        {chatOpen === "closed" && (
          <Button
            variant="secondary"
            className="absolute top-4 right-5 z-10 px-2.5 py-1 text-[12.5px]"
            onClick={() => setChatOpen("open")}
          >
            <Icon name="spark" size={13} /> Ask for help
          </Button>
        )}
        <Routes>
          <Route
            index
            element={
              <div className="flex flex-col gap-8 px-6 pt-4 pb-10">
                <header className="pr-32">
                  <h1 className="font-serif text-[23px] leading-tight">Workflows</h1>
                  <p className="mt-0.5 max-w-[46rem] font-sans text-[13px] text-muted">
                    Recipes a person has approved: pull these data, run these steps, check these things, deliver here.
                    DataLab runs them the same way every time, with no AI involved. Runs and their outputs stay on this
                    computer.
                  </p>
                </header>
                {workflows.isError ? (
                  <p className="font-sans text-[13px] text-danger">{workflows.error.message}</p>
                ) : workflows.data ? (
                  <WorkflowList workflows={workflows.data} folder={status.data?.folder} />
                ) : (
                  <p className="font-sans text-[13px] text-muted">Reading the workflows folder…</p>
                )}
                <Destinations practice={practice} />
              </div>
            }
          />
          <Route path="file" element={<FileRoute />} />
          <Route path="runs/:runId" element={<RunRoute />} />
          <Route path="*" element={<p className="px-6 py-6 font-sans text-[13px] text-muted">Nothing here.</p>} />
        </Routes>
      </main>

      {chatOpen === "open" && (
        <>
          <div className="absolute inset-0 z-20 bg-black/30 xl:hidden" onClick={() => setChatOpen("closed")} />
          <aside
            aria-label="Workflow authoring chat, in Data engineering mode"
            className="absolute inset-y-0 right-0 z-30 flex w-[min(28rem,100%)] min-h-0 flex-col border-l border-line bg-surface shadow-xl xl:static xl:w-auto xl:shadow-none"
          >
            <DockedChat
              key={chatKey}
              mode={CHAT_MODE}
              conversationId={chatId || undefined}
              onConversation={(conversation) => setChatId(conversation.id)}
              context={context}
              headerActions={chatActions}
            />
          </aside>
        </>
      )}
    </div>
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
        <Link to="/workflows" className="text-ink underline underline-offset-4">
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
function Rail() {
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: workflowsApi.list });
  const location = useLocation();
  const [params] = useSearchParams();
  const current = location.pathname.endsWith("/workflows/file") ? params.get("path") : null;
  return (
    <nav className="flex flex-col py-3">
      <Link to="/workflows" className="dl-label px-4 py-1.5 hover:text-ink">
        Workflows
      </Link>
      {workflows.data?.map((w) => (
        <Link
          key={w.path}
          to={workflowPath(w.path)}
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
