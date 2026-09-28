import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { commitUrl, type KbCommit, type KbEntry, type KbPage, knowledgeApi, type KnowledgeStatus } from "@/api/knowledge";
import { type ChatContext, DockedChat } from "@/components/chat/DockedChat";
import { InternalLinks, Markdown } from "@/components/chat/Markdown";
import { languageOf } from "@/components/chat/ProposalCard";
import { CodeEditor } from "@/components/editor/CodeEditor";
import { Button, Chip, EmptyNote, Icon, Tabs } from "@/components/ui";
import { LoadFailed, LoadingRows } from "@/components/ui/Loading";
import { CHAT_DOCK, chatClass, navClass, useKeptOnceOpen, usePanels } from "@/components/layout/panels";
import { useOverlay } from "@/components/ui/overlay";
import { useTabState } from "@/features/sql/hooks";

import { KnowledgeTree } from "./KnowledgeTree";
import { findPage, resolveLink, statusTone } from "./pages";
import { ago, repoState } from "./repoState";
import { settingsLink } from "@/features/settings/highlight";

// Knowledge writing (sessions/modes.py): helps write or tidy a page or skill,
// with the catalog tools that table and query pages cite, and no queries. Its
// edits to /work/kb come back as proposed-edit cards in this chat.
export const CHAT_MODE = "knowledge";

/** Space either side of the reading column, growing with the page between the panels: never under 16px. */
export const READING_GUTTER = "px-[clamp(16px,4cqi,48px)]";
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
  // Over the page, each is a dialog: focus in it and kept there, Escape closes it.
  // Below NAV_DOCK and CHAT_DOCK the list and the chat are shown over the page.
  const panels = usePanels("knowledge", { chatOpen: chatOpen === "open" });
  const chatKept = useKeptOnceOpen(chatOpen === "open");
  const listBeside = panels.navDocked;
  const chatBeside = panels.chatDocked;
  const drawerBox = useRef<HTMLElement>(null);
  const chatBox = useRef<HTMLElement>(null);
  const drawerOver = drawer && !listBeside;
  const chatOver = chatOpen === "open" && !chatBeside;
  useOverlay(drawerBox, drawerOver, () => setDrawer(false));
  useOverlay(chatBox, chatOver, () => setChatOpen("closed"), () => document.querySelector<HTMLElement>("[data-kb-ask]"));

  const sync = useMutation({
    mutationFn: knowledgeApi.sync,
    onSuccess: (next) => {
      queryClient.setQueryData(["knowledge-status"], next);
      for (const key of ["kb-pages", "kb-page", "kb-history"]) queryClient.invalidateQueries({ queryKey: [key] });
    },
  });
  const open = (to: string) => {
    navigate(`/knowledge/${to}`);
    setView("page");
    setDrawer(false);
  };

  // The file itself: a link or address may leave out ".md".
  const found = findPage(entries, path);
  const page = useQuery({
    queryKey: ["kb-page", found?.path, head],
    queryFn: () => knowledgeApi.page(found!.path),
    enabled: Boolean(found && head),
  });
  const context = useMemo<ChatContext | undefined>(
    () => (page.data ? { label: `The page open in the Knowledge tab (${page.data.path})`, text: page.data.text, language: languageOf(page.data.path) } : undefined),
    [page.data],
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
    <div ref={panels.ref} data-panels="knowledge" style={panels.style} className="relative grid h-full min-h-0">
      {drawerOver && <div data-scrim className="absolute inset-0 z-20 bg-black/30" onClick={() => setDrawer(false)} />}
      <aside
        ref={drawerBox}
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
          <Button variant="ghost" className={clsx("-ml-2 px-2", listBeside && "hidden")} onClick={() => setDrawer(true)} aria-label="Show pages and skills">
            <Icon name="menu" size={16} />
          </Button>
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
          ) : !path || (!findPage(entries, path) && pages.isSuccess) ? (
            <EmptyNote icon="book" title={path ? "Not in the knowledge base" : "Pick a page"}>
              {path ? `There's no ${path} in the knowledge base as last synced.` : "Choose a page or skill on the left to read it."}
            </EmptyNote>
          ) : page.error ? (
            <p className="font-sans text-[13px] text-danger">{page.error.message}</p>
          ) : page.data ? (
            <PageView page={page.data} entries={entries} repo={status.name} onOpen={open} />
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

/** One page or skill as last synced: its front matter as facts, its text rendered. */
function PageView({ page, entries, repo, onOpen }: { page: KbPage; entries: KbEntry[]; repo: string | null | undefined; onOpen: (path: string) => void }) {
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
      </header>
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

const shown = (value: unknown): string =>
  typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value);

/** A page's front matter, as facts: status and who reviewed it, the cohorts, typed evidence, limitations, related pages. */
function FrontMatter({ fields, entries, onOpen }: { fields: Record<string, unknown>; entries: KbEntry[]; onOpen: (path: string) => void }) {
  const status = typeof fields.status === "string" ? fields.status : null;
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : value === undefined || value === null ? [] : [value]);
  const known = new Set(["id", "name", "kind", "status", "summary", "description", "evidence", "limitations", "related", "cohorts", "reviewed_by", "reviewed_on"]);
  const others = Object.entries(fields).filter(([key]) => !known.has(key));
  return (
    <section className="flex flex-col gap-3 border-y border-line py-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="font-serif text-[26px] leading-tight">{shown(fields.id ?? fields.name ?? "")}</h2>
        {status && <Chip tone={statusTone(status)}>{status}</Chip>}
        {typeof fields.kind === "string" && <Chip>{fields.kind}</Chip>}
        {list(fields.cohorts).map((c) => (
          <Chip key={shown(c)}>{shown(c)}</Chip>
        ))}
      </div>
      {(fields.summary ?? fields.description) !== undefined && (
        <p className="font-serif text-[18px] leading-relaxed">{shown(fields.summary ?? fields.description)}</p>
      )}
      {status === "reviewed" && fields.reviewed_by !== undefined && (
        <p className="font-sans text-[12.5px] text-muted">
          Reviewed by @{shown(fields.reviewed_by)}
          {fields.reviewed_on !== undefined && ` on ${shown(fields.reviewed_on)}`}
        </p>
      )}
      {status === "draft" && <p className="font-sans text-[12.5px] text-attn">Draft: no one has reviewed this page yet.</p>}
      {status === "deprecated" && <p className="font-sans text-[12.5px] text-danger">Deprecated: kept for the record, not to rely on.</p>}
      <Facts title="Evidence">
        {list(fields.evidence).map((item, i) => {
          const [kind, value] = item && typeof item === "object" && !Array.isArray(item) ? (Object.entries(item)[0] ?? ["", ""]) : ["", item];
          return (
            <li key={i} className="flex flex-wrap items-baseline gap-2">
              {kind && <span className="dl-label w-16 shrink-0">{kind}</span>}
              <span className="font-mono text-[12.5px] break-all">{shown(value)}</span>
            </li>
          );
        })}
      </Facts>
      <Facts title="Limitations">
        {list(fields.limitations).map((item, i) => (
          <li key={i} className="font-sans text-[13.5px]">
            {shown(item)}
          </li>
        ))}
      </Facts>
      <Facts title="Related">
        {list(fields.related).map((item) => {
          const found = findPage(entries, shown(item));
          return (
            <li key={shown(item)} className="inline">
              {found ? (
                <button type="button" onClick={() => onOpen(found.path)} className="font-mono text-[12.5px] text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
                  {shown(item)}
                </button>
              ) : (
                <span className="font-mono text-[12.5px] text-muted">{shown(item)}</span>
              )}
            </li>
          );
        })}
      </Facts>
      {others.length > 0 && (
        <Facts title="Other fields">
          {others.map(([key, value]) => (
            <li key={key} className="font-mono text-[12.5px]">
              {key}: {shown(value)}
            </li>
          ))}
        </Facts>
      )}
    </section>
  );
}

function Facts({ title, children }: { title: string; children: ReactNode[] }) {
  if (children.length === 0) return null;
  return (
    <div>
      <h3 className="dl-label">{title}</h3>
      <ul className={clsx("mt-1 flex gap-1.5", title === "Related" ? "flex-wrap gap-x-3" : "flex-col")}>{children}</ul>
    </div>
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
