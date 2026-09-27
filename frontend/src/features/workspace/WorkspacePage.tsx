import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, type Conversation, type Mode } from "@/api/client";
import { DockedChat } from "@/components/chat/DockedChat";
import { SHOW_QUERY, showQuery, ShowQueryContext } from "@/components/chat/provenance";
import { Button, Icon, InfoTip, Modal, Panel } from "@/components/ui";
import type { AnyIcon } from "@/components/ui/Icon";
import { type OpenFile, OpenFileContext } from "@/lib/files";

import { DeleteConversation } from "./DeleteConversation";
import { ExportDialog } from "./ExportDialog";
import { FileViewer } from "./FileViewer";
import { SidePanel } from "./SidePanel";

export function WorkspacePage() {
  const { conversationId } = useParams();
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<OpenFile | null>(null);
  const [exportingReport, setExportingReport] = useState(false);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const navigate = useNavigate();
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations });
  const current = conversations.data?.find((c) => c.id === conversationId);
  // In a narrower window the conversation list (below 1024px) and the side
  // panel (below 1280px) become drawers over the page, so the reading column
  // keeps its width.
  const [drawer, setDrawer] = useState<"rail" | "panel" | null>(null);
  const railOpen = drawer === "rail";
  const panelOpen = drawer === "panel";
  const railBox = useRef<HTMLElement>(null);
  const panelBox = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  // One drawer at a time. Focus goes into it on open and back to its button on close.
  const openDrawer = (which: "rail" | "panel") => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDrawer(which);
  };
  const closeDrawer = () => setDrawer(null);
  useEffect(() => {
    if (drawer) {
      const box = drawer === "rail" ? railBox.current : panelBox.current;
      // Unless something in it already has focus (a query it was opened to show).
      if (!box?.contains(document.activeElement)) box?.querySelector<HTMLElement>("a[href], button")?.focus();
    } else if (opener.current?.isConnected) {
      opener.current.focus();
      opener.current = null;
    }
  }, [drawer]);
  useEffect(() => setDrawer(null), [conversationId]);
  // A query asked for (from a number's sources, or How was this made?): in a
  // narrower window the side panel is a drawer, so open it to show the query.
  useEffect(() => {
    const show = () => {
      if (!window.matchMedia?.("(min-width: 1280px)").matches) openDrawer("panel");
    };
    window.addEventListener(SHOW_QUERY, show);
    return () => window.removeEventListener(SHOW_QUERY, show);
  }, []);
  useEffect(() => {
    if (!railOpen && !panelOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("[role=dialog]")) closeDrawer();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railOpen, panelOpen]);
  const showRail = (
    <Button
      variant="ghost"
      className="-ml-2 px-2 lg:hidden"
      onClick={() => openDrawer("rail")}
      aria-label="Show conversations"
      aria-expanded={railOpen}
      aria-controls="conversation-list"
    >
      <Icon name="menu" size={16} />
    </Button>
  );

  return (
    <div
      className={clsx(
        "relative grid h-full min-h-0 grid-cols-[minmax(0,1fr)] lg:grid-cols-[16rem_minmax(0,1fr)]",
        conversationId && "xl:grid-cols-[16rem_minmax(0,1fr)_20rem] 2xl:grid-cols-[17rem_minmax(0,1fr)_24rem]",
      )}
    >
      {railOpen && <div className="absolute inset-0 z-20 bg-black/30 lg:hidden" onClick={closeDrawer} />}
      {panelOpen && <div className="absolute inset-0 z-20 bg-black/30 xl:hidden" onClick={closeDrawer} />}
      <aside
        id="conversation-list"
        ref={railBox}
        aria-label="Conversations"
        className={clsx(
          "min-h-0 flex-col border-r border-line bg-rail",
          railOpen ? "absolute inset-y-0 left-0 z-30 flex w-72 shadow-xl lg:static lg:w-auto lg:shadow-none" : "hidden lg:flex",
        )}
      >
        <div className="flex gap-2 px-3 pt-4 pb-2">
          <Button data-tour="new-conversation" className="flex-1 justify-start bg-surface" onClick={() => setCreating(true)}>
            <Icon name="pen" size={14} /> New conversation
          </Button>
          {railOpen && (
            <Button variant="ghost" className="px-2 lg:hidden" onClick={closeDrawer} aria-label="Close the conversation list">
              <Icon name="close" />
            </Button>
          )}
        </div>
        <Panel title="Conversations">
          <ul className="-mx-2 flex flex-col gap-px">
            {conversations.data?.map((c) => {
              const here = c.id === conversationId;
              return (
                <li key={c.id} className="group relative">
                  <Link
                    to={`/workspace/${c.id}`}
                    aria-current={here ? "page" : undefined}
                    className={clsx(
                      "relative flex items-center gap-2 rounded-[3px] py-2 pr-8 pl-3 font-sans text-[13.5px] leading-snug",
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
                  {!c.busy && (
                    <button
                      type="button"
                      onClick={() => setDeleting(c)}
                      aria-label={`Delete “${c.title}”`}
                      title="Delete this conversation"
                      className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded-[3px] p-1 text-faint opacity-0 group-hover:opacity-100 hover:text-danger focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
                    >
                      <Icon name="trash" size={13} />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </Panel>
      </aside>

      <main className="min-h-0">
        {current ? (
          <OpenFileContext value={setOpen}>
            <ShowQueryContext value={showQuery}>
              <DockedChat
                key={current.id}
                mode={current.mode}
                conversationId={current.id}
                headerStart={showRail}
                headerActions={
                  <>
                    <span data-tour="export" className="flex items-center gap-0.5">
                      <Button variant="ghost" className="px-1 text-[13px]" onClick={() => setExportingReport(true)}>
                        <Icon name="export" size={14} /> Export
                      </Button>
                      <InfoTip term="export" align="end" />
                    </span>
                    <Button
                      variant="ghost"
                      className="px-1 text-[13px] xl:hidden"
                      onClick={() => openDrawer("panel")}
                      aria-expanded={panelOpen}
                      aria-controls="conversation-files"
                    >
                      <Icon name="folder" size={14} /> Files
                    </Button>
                  </>
                }
              />
            </ShowQueryContext>
          </OpenFileContext>
        ) : (
          <div className="mx-auto flex h-full max-w-[40rem] flex-col justify-center gap-5 px-6 sm:px-8">
            <div className="lg:hidden">{showRail}</div>
            <p className="dl-label">IHS DataLab</p>
            <p className="font-serif text-[44px] leading-[1.08] tracking-[-0.01em] text-ink text-balance">
              Ask the IHS data a question.
            </p>
            <p className="max-w-[46ch] font-serif text-[18px] leading-relaxed text-muted">
              Every conversation runs in its own sealed workspace. You'll see each step the agent takes, and everything
              it reads, as it works.
            </p>
            <div>
              <Button data-tour="new-conversation" variant="primary" className="mt-2 px-4 py-2" onClick={() => setCreating(true)}>
                <Icon name="pen" size={14} /> New conversation
              </Button>
            </div>
          </div>
        )}
      </main>

      {conversationId && (
        <aside
          id="conversation-files"
          ref={panelBox}
          aria-label="Files, inputs, queries and history"
          className={clsx(
            "relative min-h-0 border-l border-line bg-rail",
            panelOpen
              ? "absolute inset-y-0 right-0 z-30 block w-[min(22rem,100%)] shadow-xl xl:static xl:w-auto xl:shadow-none"
              : "hidden xl:block",
          )}
        >
          {panelOpen && (
            <Button variant="ghost" className="absolute top-1.5 right-1.5 z-10 px-2 xl:hidden" onClick={closeDrawer} aria-label="Close the files panel">
              <Icon name="close" />
            </Button>
          )}
          {current && <SidePanel key={current.id} conversation={current} onOpen={setOpen} />}
        </aside>
      )}

      {current && exportingReport && (
        <ExportDialog conversation={current} withReport onClose={() => setExportingReport(false)} />
      )}
      {current && open && (
        <FileViewer
          // A new file, or another version, is a new viewer: nothing carries over.
          key={`${open.root}:${open.path}:${open.checkpoint ?? ""}`}
          conversationId={current.id}
          file={open}
          onOpen={setOpen}
          onClose={() => setOpen(null)}
        />
      )}

      {creating && <NewConversation onClose={() => setCreating(false)} />}
      {deleting && (
        <DeleteConversation
          conversation={conversations.data?.find((c) => c.id === deleting.id) ?? deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            // Leaving a deleted conversation's page for the workspace's start.
            if (deleting.id === conversationId) navigate("/workspace");
            setDeleting(null);
          }}
        />
      )}
    </div>
  );
}

function NewConversation({ onClose }: { onClose: () => void }) {
  const modes = useQuery({ queryKey: ["modes"], queryFn: api.modes });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 5 * 60_000 });
  const [mode, setMode] = useState("analysis");
  const [model, setModel] = useState("");
  const [choosingModel, setChoosingModel] = useState(false);
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
    mutationFn: () => api.createConversation(mode, model || undefined),
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
            {/* Modes a tab docks (Workflow authoring, Knowledge writing) open from that tab. */}
            {modes.data
              ?.filter((m) => !m.tab_only)
              .map((m) => <ModeCard key={m.id} mode={m} selected={mode === m.id} onSelect={() => setMode(m.id)} />)}
          </div>
        </div>
        {/* No title to type: DataLab names the conversation from its first question. */}
        {choosingModel ? (
          <label className="block">
            <span className={label}>Model</span>
            <select
              autoFocus
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
        ) : (
          <p className="text-[13px] text-muted">
            Model: <span className="font-mono text-[12px] text-ink">{model || (choices.includes(models.data?.default ?? "") ? models.data?.default : choices[0]) || "" || "…"}</span>
            {models.data && (model || (choices.includes(models.data.default) ? models.data.default : choices[0])) === models.data.default && " (recommended)"}{" "}
            <button type="button" onClick={() => setChoosingModel(true)} className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
              Change
            </button>
          </p>
        )}
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
