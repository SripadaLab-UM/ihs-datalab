import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { commitUrl, type KbCommit, type KbEntry, type KbPage, knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";
import { type ChatContext, type ChatRequest, DockedChat } from "@/components/chat/DockedChat";
import { InternalLinks, Markdown } from "@/components/chat/Markdown";
import { languageOf } from "@/components/chat/ProposalCard";
import { CodeEditor } from "@/components/editor/CodeEditor";
import { Button, Chip, EmptyNote, Icon, Tabs } from "@/components/ui";
import { LoadFailed, LoadingRows } from "@/components/ui/Loading";
import { CHAT_DOCK, chatClass, messageBox, navClass, useKeptOnceOpen, usePanels } from "@/components/layout/panels";
import { useOverlay } from "@/components/ui/overlay";
import { refreshCatalog } from "@/features/sql/catalogRefresh";
import { useTabState } from "@/features/sql/hooks";

import { FrontMatter } from "./FrontMatter";
import { KnowledgeTree } from "./KnowledgeTree";
import { type EditorView, PageEditor } from "./PageEditor";
import { findPage, resolveLink } from "./pages";
import { ago, repoState } from "./repoState";
import { settingsLink } from "@/features/settings/highlight";

// Knowledge writing (sessions/modes.py): helps write or tidy a page or skill,
// with the catalog tools that table and query pages cite, and no queries. Its
// edits to /work/kb come back as proposed-edit cards in this chat.
export const CHAT_MODE = "knowledge";

/** Space either side of the reading column, growing with the page between the panels: never under 16px
 *  (16px where the browser has no container units). */
export const READING_GUTTER = "px-4 supports-[width:1cqi]:px-[clamp(16px,4cqi,48px)]";
/** The reading column: centred, as wide as a comfortable line of the page's text (running text is kept to 72ch
 *  within it, `.dl-reading`); title, facts and body share its left edge. */
export const READING_COLUMN = "dl-reading mx-auto w-full min-w-0 max-w-[46rem]";

/** Knowledge pages, lab skills and their change history, with a chat that helps write or tidy a page or skill. */
export function KnowledgePage() {
  const status = useQuery({ queryKey: ["knowledge-status"], queryFn: knowledgeApi.status });
  if (!status.data) {
    return (
      <p className="flex h-full items-center justify-center font-sans text-[13px] text-muted">
        {status.error ? status.error.message : "Opening the knowledge base…"}
      </p>
    );
  }
  if (!status.data.available) return <Unavailable status={status.data} />;
  return <KnowledgeBase status={status.data} />;
}

function Unavailable({ status }: { status: KnowledgeStatus }) {
  return (
    <div className="mx-auto flex h-full max-w-xl flex-col justify-center gap-3 px-8">
      <h1 className="font-serif text-[34px]">Knowledge</h1>
      <p className="font-serif text-[18px] leading-relaxed text-muted">
        The lab's shared knowledge about IHS data: what tables and variables mean, device quirks, QC rules, cohorts and
        verified queries. Agents read it, and anyone in the lab can improve it.
      </p>
      <p className="font-sans text-[14px]">{status.message}</p>
    </div>
  );
}

function KnowledgeBase({ status }: { status: KnowledgeStatus }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const selected = useParams()["*"] ?? "";
  const pages = useQuery({ queryKey: ["kb-pages"], queryFn: knowledgeApi.pages });
  const entries = useMemo(() => pages.data?.pages ?? [], [pages.data]);
  const head = pages.data?.head ?? null;
  // Nothing chosen: the index, the knowledge base's own table of contents.
  const path = selected || (findPage(entries, "index.md") ? "index.md" : "");
  const [view, setView] = useState<"page" | "history">("page");
  const [drawer, setDrawer] = useState(false);
  const [chatOpen, setChatOpen] = useTabState<"open" | "closed">(
    "datalab:kb:chat-open",
    typeof window !== "undefined" && window.matchMedia?.(CHAT_DOCK).matches ? "open" : "closed",
  );
  const [chatId, setChatId] = useTabState("datalab:kb:chat", "");
  const [chatKey, setChatKey] = useState(0);
  // Edit with agent: the chat gets the page as context, ticked, and the cursor.
  const [chatRequest, setChatRequest] = useState<ChatRequest | undefined>(undefined);
  // Edit page: ?edit=1 edits the page open (?edit=<id>: that edit), and survives a reload.
  const [search, setSearch] = useSearchParams();
  const editing = search.get("edit");
  const editView = (["edit", "preview", "review"] as const).find((v) => v === search.get("view")) ?? "edit";
  // Over the page, each is a dialog: focus in it and kept there, Escape closes it.
  // Below NAV_DOCK and CHAT_DOCK the list and the chat are shown over the page.
  const panels = usePanels("knowledge", { chatOpen: chatOpen === "open" });
  const chatKept = useKeptOnceOpen(chatOpen === "open");
  const listBeside = panels.navDocked;
  const chatBeside = panels.chatDocked;
  const drawerBox = useRef<HTMLElement>(null);
  const chatBox = useRef<HTMLElement>(null);
  // Docked again (the window widened), the list is no longer a drawer to close.
  if (drawer && listBeside) setDrawer(false);
  const drawerOver = drawer && !listBeside;
  const chatOver = chatOpen === "open" && !chatBeside;
  useOverlay(drawerBox, drawerOver, () => setDrawer(false));
  useOverlay(chatBox, chatOver, () => setChatOpen("closed"), () => document.querySelector<HTMLElement>("[data-kb-ask]"), messageBox);

  const sync = useMutation({
    mutationFn: knowledgeApi.sync,
    onSuccess: (next) => {
      queryClient.setQueryData(["knowledge-status"], next);
      for (const key of ["kb-pages", "kb-page", "kb-history"]) queryClient.invalidateQueries({ queryKey: [key] });
      // The table catalog comes from the knowledge base.
      refreshCatalog(queryClient);
    },
  });
  const open = (to: string) => {
    navigate(`/knowledge/${to}`);
    setView("page");
    setDrawer(false);
  };
  const startEditing = (view: EditorView = "edit") => {
    setView("page");
    setSearch({ edit: "1", ...(view !== "edit" ? { view } : {}) });
  };
  const stopEditing = () => setSearch({});
  // Drafts on this computer: a page with one says so, and "Edit page" continues it.
  const drafts = useQuery({ queryKey: ["kb-edits"], queryFn: knowledgeApi.edits, enabled: Boolean(head) });

  // The file itself: a link or address may leave out ".md".
  const found = findPage(entries, path);
  const page = useQuery({
    queryKey: ["kb-page", found?.path, head],
    queryFn: () => knowledgeApi.page(found!.path),
    enabled: Boolean(found && head),
  });
  const context = useMemo<ChatContext | undefined>(
    () => (page.data ? { label: `The page open in the Knowledge tab (${page.data.path})`, name: page.data.path, text: page.data.text, language: languageOf(page.data.path) } : undefined),
    [page.data],
  );
  const editWithAgent = (target: string) => {
    setChatOpen("open");
    setChatRequest({ key: Date.now(), placeholder: `Describe the change to ${target}` });
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

  return (
    <div ref={panels.ref} data-panels="knowledge" style={panels.style} className="relative grid h-full min-h-0">
      {drawerOver && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setDrawer(false)} />}
      <aside
        ref={drawerBox}
        id={panels.navId}
        aria-label="Pages and skills"
        role={drawerOver ? "dialog" : undefined}
        aria-modal={drawerOver || undefined}
        tabIndex={-1}
        className={navClass(listBeside, drawer)}
      >
        <KnowledgeTree entries={entries} selected={found?.path ?? path} reveal={Boolean(selected)} onOpen={open} loading={pages.isPending} />
      </aside>

      {/* A size container: the reading column's margins follow the page's own width, as the panels beside it change. */}
      <main className="@container flex min-h-0 min-w-0 flex-col">
        <header className="flex items-start gap-3 px-5 pt-4 pb-3">
          {!listBeside && (
            <Button variant="ghost" className="-ml-2 px-2" onClick={() => setDrawer(true)} aria-label="Show pages and skills">
              <Icon name="menu" size={16} />
            </Button>
          )}
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-[23px] leading-tight">Knowledge</h1>
            <RepoLine status={status} onSync={() => sync.mutate()} syncing={sync.isPending} error={sync.error?.message} />
          </div>
          {chatOpen === "closed" && (
            <Button data-kb-ask variant="secondary" className="shrink-0 px-2.5 py-1 text-[12.5px]" onClick={() => setChatOpen("open")}>
              <Icon name="spark" size={13} /> Ask for help
            </Button>
          )}
        </header>
        <Tabs
          tabs={[
            { id: "page", label: "Page" },
            { id: "history", label: "Recent changes" },
          ]}
          value={view}
          onChange={setView}
        />
        <div data-reading-scroll className={clsx("min-h-0 flex-1 overflow-x-hidden overflow-y-auto py-5", READING_GUTTER)}>
          {view === "history" ? (
            <History repo={status.name} entries={entries} onOpen={open} />
          ) : pages.isPending ? (
            // Never "Nothing here yet" before the pages are in.
            <LoadingRows label="Loading the knowledge base…" rows={4} className={READING_COLUMN} />
          ) : pages.isError && !pages.data ? (
            <div className={READING_COLUMN}>
              <LoadFailed message={`The pages couldn't be read: ${pages.error.message}`} onRetry={() => void pages.refetch()} retrying={pages.isFetching} />
            </div>
          ) : !head ? (
            <EmptyNote icon="book" title="Nothing here yet">
              {status.signed_in ? "Press Sync to download the knowledge base from GitHub." : <SignInFirst />}
            </EmptyNote>
          ) : editing && path ? (
            <div className={READING_COLUMN}>
              <PageEditor
                key={`${path}:${editing}`}
                path={found?.path ?? path}
                editId={editing !== "1" && editing !== "new" ? editing : undefined}
                isNew={editing === "new"}
                initialView={editView}
                repo={status.name}
                entries={entries}
                signedIn={status.signed_in}
                onOpen={open}
                onClose={stopEditing}
              />
            </div>
          ) : !path || (!findPage(entries, path) && pages.isSuccess) ? (
            <EmptyNote icon="book" title={path ? "Not in the knowledge base" : "Pick a page"}>
              {path ? `There's no ${path} in the knowledge base as last synced.` : "Choose a page or skill on the left to read it."}
            </EmptyNote>
          ) : page.error ? (
            <p className="font-sans text-[13px] text-danger">{page.error.message}</p>
          ) : page.data ? (
            <PageView
              page={page.data}
              entries={entries}
              repo={status.name}
              onOpen={open}
              draft={drafts.data?.some((d) => d.path === page.data.path) ?? false}
              onEdit={() => startEditing()}
              onEditWithAgent={() => editWithAgent(page.data.path)}
            />
          ) : (
            <p className="font-sans text-[13px] text-muted">Opening {path}…</p>
          )}
        </div>
      </main>

      {chatOver && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setChatOpen("closed")} />}
      {/* Kept mounted while hidden, so closing and reopening it keeps its draft and place. */}
      {chatKept && (
        <aside
          ref={chatBox}
          id={panels.chatId}
          aria-label="Knowledge chat"
          hidden={chatOpen !== "open"}
          role={chatOver ? "dialog" : undefined}
          aria-modal={chatOver || undefined}
          tabIndex={-1}
          className={chatClass(chatBeside)}
        >
          <DockedChat
            key={chatKey}
            mode={CHAT_MODE}
            conversationId={chatId || undefined}
            onConversation={(conversation) => setChatId(conversation.id)}
            context={context}
            headerActions={chatActions}
            assistant="knowledge"
            active={chatOpen === "open"}
            request={chatRequest}
          />
        </aside>
      )}
      {panels.dividers}
    </div>
  );
}

function SignInFirst() {
  return (
    <>
      Sign in to GitHub first:{" "}
      <Link {...settingsLink("connections", "connection-github")} className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
        Settings → Connections → GitHub
      </Link>
      .
    </>
  );
}

/** Where the copy on this computer is: the repo, how it stands against GitHub, the last sync, and Sync. */
function RepoLine({ status, onSync, syncing, error }: { status: KnowledgeStatus; onSync: () => void; syncing: boolean; error?: string }) {
  const repo = repoState(status);
  return (
    <div className="mt-1 flex flex-col gap-1 font-sans text-[13px]">
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-muted">
        <span className="font-mono text-[12.5px] text-ink">{status.name}</span>
        <Chip tone={repo.tone}>{repo.label}</Chip>
        {status.last_sync && (
          <span title={new Date(status.last_sync).toLocaleString()}>synced {ago(status.last_sync)}</span>
        )}
        {status.head && <span className="font-mono text-[12px]">{status.head.slice(0, 7)}</span>}
        {repo.canSync && (
          <Button variant="ghost" className="px-1.5 py-0.5 text-[12.5px]" onClick={onSync} disabled={syncing}>
            <Icon name="restore" size={12} /> {syncing ? "Syncing…" : "Sync"}
          </Button>
        )}
      </p>
      {status.repo === "signed out" ? (
        <p className="text-muted">
          {repo.text}{" "}
          <Link {...settingsLink("connections", "connection-github")} className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
            Sign in
          </Link>
        </p>
      ) : (
        status.repo !== "in sync" && <p className={repo.tone === "bad" ? "text-danger" : "text-muted"}>{repo.text}</p>
      )}
      {error && <p className="text-danger">{error}</p>}
    </div>
  );
}

/** One page or skill as last synced: its front matter as facts, its text rendered; and how to change it. */
function PageView({
  page,
  entries,
  repo,
  onOpen,
  draft,
  onEdit,
  onEditWithAgent,
}: {
  page: KbPage;
  entries: KbEntry[];
  repo: string | null | undefined;
  onOpen: (path: string) => void;
  /** There's a draft of it on this computer. */
  draft: boolean;
  onEdit: () => void;
  onEditWithAgent: () => void;
}) {
  const follow = (href: string) => {
    const target = resolveLink(page.path, href);
    const found = target ? findPage(entries, target) : undefined;
    return found ? () => onOpen(found.path) : null;
  };
  const github = repo && /^[\w.-]+\/[\w.-]+$/.test(repo) ? `https://github.com/${repo}/blob/${page.head}/${page.path}` : null;
  const markdown = page.path.endsWith(".md");
  return (
    <article data-reading-column className={clsx(READING_COLUMN, "flex flex-col gap-5")}>
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-[12.5px] text-muted">{page.path}</span>
        {github && (
          <a href={github} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-sans text-[12.5px] text-muted underline decoration-faint underline-offset-4 hover:text-ink">
            On GitHub <Icon name="open" size={11} />
          </a>
        )}
        {page.editable && (
          <span className="ml-auto flex flex-wrap items-center gap-2">
            <Button className="px-2.5 py-1 text-[12.5px]" onClick={onEdit}>
              <Icon name="pen" size={12} /> {draft ? "Continue editing" : "Edit page"}
            </Button>
            <Button variant="ghost" className="px-2 py-1 text-[12.5px]" onClick={onEditWithAgent}>
              <Icon name="spark" size={12} /> Edit with agent
            </Button>
          </span>
        )}
      </header>
      {page.editable && draft && (
        <p className="-mt-3 font-sans text-[12.5px] text-you">You have a draft of this page on this computer, not shared yet.</p>
      )}
      {!page.editable && page.source_note && (
        <p role="note" className="-mt-2 flex items-baseline gap-2 rounded-[3px] border border-line bg-sunken px-3 py-2 font-sans text-[13px] text-muted">
          <Icon name="lock" size={12} className="shrink-0 translate-y-[1px]" />
          <span>
            <span className="text-ink">Read-only here.</span> {page.source_note}
          </span>
        </p>
      )}
      {page.front_matter && <FrontMatter fields={page.front_matter} entries={entries} onOpen={onOpen} />}
      {markdown ? (
        <InternalLinks value={follow}>
          <Markdown text={page.body} />
        </InternalLinks>
      ) : (
        <CodeEditor label={page.path} language={languageOf(page.path)} value={page.text} readOnly className="h-[60vh]" />
      )}
    </article>
  );
}

/** The latest commits of the knowledge base, as last synced. */
function History({ repo, entries, onOpen }: { repo: string | null | undefined; entries: KbEntry[]; onOpen: (path: string) => void }) {
  const history = useQuery({ queryKey: ["kb-history"], queryFn: () => knowledgeApi.history(30) });
  if (history.error) return <p className="font-sans text-[13px] text-danger">{history.error.message}</p>;
  if (!history.data) return <p className="font-sans text-[13px] text-muted">Loading the history…</p>;
  if (history.data.length === 0) {
    return (
      <EmptyNote icon="history" title="No history yet">
        Changes appear here once the knowledge base is synced.
      </EmptyNote>
    );
  }
  return (
    <ol className={clsx(READING_COLUMN, "flex flex-col")}>
      {history.data.map((commit) => (
        <CommitRow key={commit.commit} commit={commit} repo={repo} entries={entries} onOpen={onOpen} />
      ))}
    </ol>
  );
}

function CommitRow({ commit, repo, entries, onOpen }: { commit: KbCommit; repo: string | null | undefined; entries: KbEntry[]; onOpen: (path: string) => void }) {
  const url = commitUrl(repo, commit.commit);
  return (
    <li className="flex flex-col gap-1 border-b border-line py-3">
      <p className="font-serif text-[17px] leading-snug">{commit.subject}</p>
      <p className="flex flex-wrap items-baseline gap-x-2 font-sans text-[12.5px] text-muted">
        <span>{commit.author}</span>
        <span title={new Date(commit.date).toLocaleString()}>{ago(commit.date)}</span>
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer" className="font-mono text-[12px] underline decoration-faint underline-offset-4 hover:text-ink">
            {commit.commit.slice(0, 7)}
          </a>
        ) : (
          <span className="font-mono text-[12px]">{commit.commit.slice(0, 7)}</span>
        )}
      </p>
      <p className="flex flex-wrap gap-1.5">
        {commit.paths.map((path) =>
          findPage(entries, path) ? (
            <button key={path} type="button" onClick={() => onOpen(path)} className="font-mono text-[12px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
              {path}
            </button>
          ) : (
            <span key={path} className="font-mono text-[12px] text-muted">
              {path}
            </span>
          ),
        )}
        {commit.changed > commit.paths.length && (
          <span className="font-sans text-[12px] text-muted">and {commit.changed - commit.paths.length} more</span>
        )}
      </p>
    </li>
  );
}
