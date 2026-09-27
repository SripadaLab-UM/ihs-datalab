import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, type Mode } from "@/api/client";
import { Chat } from "@/components/chat/Chat";
import { Button, Icon, Modal, Panel } from "@/components/ui";
import type { AnyIcon } from "@/components/ui/Icon";
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
      <aside className="flex min-h-0 flex-col border-r border-line">
        <div className="px-5 pt-6 pb-4">
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="font-serif text-[16px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
          >
            Start a new question
          </button>
        </div>
        <Panel title="Conversations">
          <ul className="flex flex-col border-t border-line">
            {conversations.data?.map((c) => (
              <li key={c.id} className="border-b border-line">
                <Link
                  to={`/workspace/${c.id}`}
                  aria-current={c.id === conversationId ? "page" : undefined}
                  className={clsx(
                    "flex flex-col gap-0.5 py-2.5 font-serif text-[15px] leading-snug",
                    c.id === conversationId ? "text-ink" : "text-muted hover:text-ink",
                  )}
                >
                  <span className="flex items-baseline gap-2">
                    <span className="line-clamp-2 flex-1 break-words">{c.title}</span>
                    {c.busy && <span className="dl-breathe size-[7px] shrink-0 rounded-full bg-ink" title="Working" />}
                  </span>
                  <span className={clsx("dl-label", c.kind === "data" ? "!text-data" : "!text-research")}>
                    {c.kind === "data" ? "data · web blocked" : "research · no database"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      </aside>

      <main className="min-h-0">
        {current ? (
          <OpenFileContext value={setOpen}>
            <Chat
              key={current.id}
              conversation={current}
              headerActions={
                <Button variant="ghost" className="px-1 text-[13px]" onClick={() => setExportingReport(true)}>
                  <Icon name="export" size={14} /> Export
                </Button>
              }
            />
          </OpenFileContext>
        ) : (
          <div className="mx-auto flex h-full max-w-[40rem] flex-col justify-center gap-5 px-8">
            <p className="dl-label">IHS DataLab</p>
            <p className="font-serif text-[44px] leading-[1.08] tracking-[-0.01em] text-ink text-balance">
              Ask the IHS data a question.
            </p>
            <p className="max-w-[46ch] font-serif text-[18px] leading-relaxed text-muted">
              Every conversation runs in its own sealed workspace. You'll see each step the agent takes, and everything
              it reads, as it works.
            </p>
            <div>
              <Button variant="primary" className="mt-2 px-4 py-2" onClick={() => setCreating(true)}>
                Start a new question
              </Button>
            </div>
          </div>
        )}
      </main>

      <aside className="min-h-0 border-l border-line">
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
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 5 * 60_000 });
  const [mode, setMode] = useState("analysis");
  const [title, setTitle] = useState("");
  const [model, setModel] = useState("");
  // The default is offered while the list loads, or if U-M can't be asked.
  const choices = !models.data
    ? []
    : models.data.available.length === 0
      ? [models.data.default]
      : models.data.available.includes(models.data.default)
        ? [models.data.default, ...models.data.available.filter((m) => m !== models.data.default)]
        : models.data.available;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () => api.createConversation(mode, title.trim() || "New conversation", model || undefined),
    onSuccess: (conversation) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/workspace/${conversation.id}`);
      onClose();
    },
  });

  const chosen = modes.data?.find((m) => m.id === mode);
  const field = "mt-1.5 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:border-accent";
  const label = "text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";
  return (
    <Modal title="New conversation" onClose={onClose}>
      <form
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div>
          <p className={label}>What would you like to do?</p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {modes.data?.map((m) => <ModeCard key={m.id} mode={m} selected={mode === m.id} onSelect={() => setMode(m.id)} />)}
          </div>
        </div>
        <label className="block">
          <span className={label}>Title</span>
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Sleep and mood in 2025 interns"
            className={field}
          />
        </label>
        <label className="block">
          <span className={label}>Model</span>
          <select
            value={model || (choices.includes(models.data?.default ?? "") ? models.data?.default : choices[0]) || ""}
            onChange={(e) => setModel(e.target.value)}
            className={field}
          >
            {choices.length === 0 && <option value="">Loading…</option>}
            {choices.map((m) => (
              <option key={m} value={m}>
                {m}
                {m === models.data?.default ? " (recommended)" : ""}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-muted">Only models approved for study data are offered.</span>
        </label>
        {chosen && (
          <div
            className={clsx(
              "flex gap-2.5 rounded-xl p-3 text-xs",
              chosen.kind === "research" ? "bg-research-soft text-research" : "bg-data-soft text-data",
            )}
          >
            <Icon name={chosen.kind === "research" ? "globe" : "lock"} size={16} className="mt-px shrink-0" />
            <span>
              {chosen.kind === "research"
                ? "Research sessions can use the web but have no connection to the study database. Anything you attach may reach the web."
                : "Data sessions can query the study database, read-only. Websites are blocked; the model runs on U-M's approved GPT service."}
            </span>
          </div>
        )}
        {create.error && <p className="text-sm text-danger">{create.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={create.isPending}>
            {create.isPending ? "Starting…" : "Start"} <Icon name="send" size={14} />
          </Button>
        </div>
      </form>
    </Modal>
  );
}

const MODE_ICONS: Record<string, AnyIcon> = { analysis: "chart", extraction: "table", engineering: "code", research: "globe" };

function ModeCard({ mode, selected, onSelect }: { mode: Mode; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={clsx(
        "flex flex-col gap-1.5 rounded-xl border p-3 text-left transition-colors",
        selected ? "border-accent bg-accent-soft" : "border-line hover:bg-sunken",
      )}
    >
      <span className="flex items-center gap-2">
        <span
          className={clsx(
            "inline-flex h-7 w-7 items-center justify-center rounded-lg",
            selected ? "bg-accent text-accent-ink" : "bg-sunken text-muted",
          )}
        >
          <Icon name={MODE_ICONS[mode.id] ?? "spark"} size={15} />
        </span>
        <span className="flex-1 text-sm font-medium">{mode.label}</span>
        <Icon
          name={mode.kind === "data" ? "lock" : "globe"}
          size={13}
          className={mode.kind === "data" ? "text-data" : "text-research"}
        />
      </span>
      <span className="text-xs leading-snug text-muted">{mode.description}</span>
    </button>
  );
}
