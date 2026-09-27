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
    <div className={clsx("grid h-full min-h-0", conversationId ? "grid-cols-[16rem_1fr_20rem]" : "grid-cols-[16rem_1fr]")}>
      <aside className="flex min-h-0 flex-col border-r border-line bg-rail">
        <div className="px-3 pt-4 pb-2">
          <Button className="w-full justify-start bg-surface" onClick={() => setCreating(true)}>
            <Icon name="pen" size={14} /> New conversation
          </Button>
        </div>
        <Panel title="Conversations">
          <ul className="-mx-2 flex flex-col gap-px">
            {conversations.data?.map((c) => {
              const here = c.id === conversationId;
              return (
                <li key={c.id}>
                  <Link
                    to={`/workspace/${c.id}`}
                    aria-current={here ? "page" : undefined}
                    className={clsx(
                      "relative flex items-center gap-2 rounded-[3px] py-2 pr-2 pl-3 font-sans text-[13.5px] leading-snug",
                      here
                        ? "bg-surface font-medium text-ink shadow-[inset_0_0_0_1px_var(--color-line)] before:absolute before:inset-y-1.5 before:left-0 before:w-[3px] before:rounded-full before:bg-ink"
                        : "text-muted hover:bg-surface/70 hover:text-ink",
                    )}
                  >
                    <span
                      className={clsx("shrink-0", c.kind === "data" ? "text-data" : "text-research")}
                      title={
                        c.kind === "data"
                          ? "Data session: database access, web blocked"
                          : "Research session: web access, no database connection"
                      }
                    >
                      <Icon name={c.kind === "data" ? "lock" : "globe"} size={13} />
                    </span>
                    <span className="sr-only">{c.kind === "data" ? "Data session:" : "Research session:"}</span>
                    <span className="line-clamp-2 min-w-0 flex-1 break-words">{c.title}</span>
                    {c.busy && (
                      <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted" title="The agent is working">
                        <span className="dl-breathe size-[6px] rounded-full bg-ink" /> working
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
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
                <Icon name="pen" size={14} /> New conversation
              </Button>
            </div>
          </div>
        )}
      </main>

      {conversationId && (
        <aside className="min-h-0 border-l border-line bg-rail">
          {current && <SidePanel key={current.id} conversation={current} onOpen={setOpen} />}
        </aside>
      )}

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
                ? "Research sessions have the internet but no study data. Anything you attach may reach the internet."
                : "Data sessions can query the study database, read-only, but have no internet."}
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
