import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, type Mode } from "@/api/client";
import { Chat } from "@/components/chat/Chat";
import { Button, Panel } from "@/components/ui";
import { type OpenFile, OpenFileContext } from "@/lib/files";

import { ExportDialog } from "./ExportDialog";
import { FileViewer } from "./FileViewer";
import { SidePanel } from "./SidePanel";

export function WorkspacePage() {
  const { conversationId } = useParams();
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<OpenFile | null>(null);
  const [exportingReport, setExportingReport] = useState(false);
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations });
  const current = conversations.data?.find((c) => c.id === conversationId);

  return (
    <div className="grid h-full min-h-0 grid-cols-[16rem_1fr_20rem]">
      <aside className="flex min-h-0 flex-col border-r border-line bg-surface">
        <div className="p-3">
          <Button variant="primary" className="w-full justify-center" onClick={() => setCreating(true)}>
            New conversation
          </Button>
        </div>
        <Panel title="Conversations">
          <ul className="flex flex-col gap-0.5">
            {conversations.data?.map((c) => (
              <li key={c.id}>
                <Link
                  to={`/workspace/${c.id}`}
                  className={clsx(
                    "flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-sunken",
                    c.id === conversationId && "bg-sunken font-medium",
                  )}
                >
                  <span aria-label={c.kind === "data" ? "data session" : "research session"}>
                    {c.kind === "data" ? "🔒" : "🌐"}
                  </span>
                  <span className="truncate">{c.title}</span>
                  {c.busy && <span className="ml-auto h-2 w-2 animate-pulse rounded-full bg-accent" />}
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      </aside>

      <main className="min-h-0 bg-canvas">
        {current ? (
          <OpenFileContext value={setOpen}>
            <Chat
              key={current.id}
              conversation={current}
              headerActions={
                <Button variant="ghost" className="text-xs" onClick={() => setExportingReport(true)}>
                  Export conversation
                </Button>
              }
            />
          </OpenFileContext>
        ) : (
          <div className="flex h-full items-center justify-center text-muted">
            Start a new conversation, or pick one on the left.
          </div>
        )}
      </main>

      <aside className="min-h-0 border-l border-line bg-surface">
        {current && <SidePanel key={current.id} conversation={current} onOpen={setOpen} />}
      </aside>

      {current && exportingReport && (
        <ExportDialog conversation={current} withReport onClose={() => setExportingReport(false)} />
      )}
      {current && open && <FileViewer conversationId={current.id} file={open} onClose={() => setOpen(null)} />}

      {creating && <NewConversation onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewConversation({ onClose }: { onClose: () => void }) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const [mode, setMode] = useState("analysis");
  const [title, setTitle] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () => api.createConversation(mode, title.trim() || "New conversation"),
    onSuccess: (conversation) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/workspace/${conversation.id}`);
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div className="w-[34rem] rounded-2xl bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">New conversation</h2>
        <label className="mt-4 block text-sm">
          Title
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Sleep and mood in 2025 interns"
            className="mt-1 w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm outline-none focus:border-accent"
          />
        </label>
        <p className="mt-4 text-sm">Mode</p>
        <div className="mt-1 grid grid-cols-2 gap-2">
          {modes.data?.map((m) => <ModeCard key={m.id} mode={m} selected={mode === m.id} onSelect={() => setMode(m.id)} />)}
        </div>
        <p className="mt-3 text-xs text-muted">
          {mode === "research"
            ? "🌐 Research sessions have the internet but no study data. Anything you attach may reach the internet."
            : "🔒 Data sessions can query the study database (read-only) but have no internet."}
        </p>
        {create.error && <p className="mt-2 text-sm text-danger">{create.error.message}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => create.mutate()} disabled={create.isPending}>
            Start
          </Button>
        </div>
      </div>
    </div>
  );
}

function ModeCard({ mode, selected, onSelect }: { mode: Mode; selected: boolean; onSelect: () => void }) {
  return (
    <button
      onClick={onSelect}
      className={clsx(
        "rounded-xl border p-3 text-left text-sm",
        selected ? "border-accent bg-accent-soft" : "border-line hover:bg-sunken",
      )}
    >
      <span className="font-medium">{mode.label}</span>
      <span className="ml-2 text-xs text-muted">{mode.kind === "data" ? "🔒 data" : "🌐 research"}</span>
    </button>
  );
}
