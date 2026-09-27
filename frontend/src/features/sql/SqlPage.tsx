import type { Extension } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/api/client";
import { type HistoryItem, type SqlCheck, type SqlRun, sqlApi } from "@/api/sql";
import { type ChatContext, DockedChat } from "@/components/chat/DockedChat";
import { CodeEditor } from "@/components/editor/CodeEditor";
import { Button, Icon, Tabs } from "@/components/ui";

import { CatalogBrowser } from "./CatalogBrowser";
import { History } from "./History";
import { useElapsed, useRun, useSqlCheck, useTabState } from "./hooks";
import { formatSeconds, NothingYet, ResultGrid, ResultNote, type ShownResult, submitKeys } from "./Results";

type Bridge = typeof import("./editorBridge");

const WIDE = "(min-width: 1280px)";

/** The SQL Playground: your own SQL editor, results preview and catalog browser, with a Data extraction chat docked beside it. */
export function SqlPage() {
  const status = useQuery({ queryKey: ["sql-status"], queryFn: sqlApi.status });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  const previewRows = status.data?.preview_rows ?? 200;

  const [sql, setSql] = useTabState("datalab:sql:draft", "");
  const { check, diagnostics } = useSqlCheck(sql);
  const bindNames = useBindNames(sql, check);
  const [binds, setBinds] = useState<Record<string, string>>({});
  const runner = useRun();
  const elapsed = useElapsed(runner.startedAt);
  // A query opened from history, shown instead of the latest run.
  const [opened, setOpened] = useState<HistoryItem | null>(null);

  const [side, setSide] = useState<"tables" | "history">("tables");
  const [drawer, setDrawer] = useState(false);
  const [chatOpen, setChatOpen] = useTabState<"open" | "closed">(
    "datalab:sql:chat-open",
    typeof window !== "undefined" && window.matchMedia?.(WIDE).matches ? "open" : "closed",
  );
  const [chatId, setChatId] = useTabState("datalab:sql:chat", "");
  const [chatKey, setChatKey] = useState(0);
  useForgetMissingChat(chatId, () => setChatId(""));

  // Inserting names from the catalog at the cursor, through the editor's own view.
  const holder = useRef<{ view: EditorView | null }>({ view: null });
  const bridge = useRef<Bridge | null>(null);
  const [extensions, setExtensions] = useState<Extension[]>([]);
  useEffect(() => {
    let cancelled = false;
    import("./editorBridge").then((module) => {
      if (cancelled) return;
      bridge.current = module;
      setExtensions([module.bridge(holder.current)]);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  const insert = (text: string) => {
    const view = holder.current.view;
    if (view && bridge.current) bridge.current.insertAt(view, text);
    else setSql(sql ? `${sql} ${text}` : text);
    setDrawer(false);
  };

  const run = () => {
    if (!sql.trim() || runner.running) return;
    setOpened(null);
    void runner.start(sql, Object.fromEntries(bindNames.map((name) => [name, binds[name] ?? ""])));
  };

  const openHistory = (item: HistoryItem) => {
    runner.clear();
    setSql(item.sql);
    setBinds(Object.fromEntries(Object.entries(item.binds).map(([k, v]) => [k, v === null ? "" : String(v)])));
    setOpened(item);
    setDrawer(false);
  };

  const context = useMemo<ChatContext>(() => ({ label: "The query in the SQL editor", text: sql, language: "sql" }), [sql]);

  const sidePanel = (
    <div className="flex h-full min-h-0 flex-col">
      <Tabs
        tabs={[
          { id: "tables", label: "Tables" },
          { id: "history", label: "History" },
        ]}
        value={side}
        onChange={setSide}
      />
      {side === "tables" ? <CatalogBrowser onInsert={insert} /> : <History onOpen={openHistory} />}
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
    <div
      className={clsx(
        "relative grid h-full min-h-0 grid-cols-[minmax(0,1fr)] lg:grid-cols-[17rem_minmax(0,1fr)]",
        chatOpen === "open" && "xl:grid-cols-[17rem_minmax(0,1fr)_26rem] 2xl:grid-cols-[18rem_minmax(0,1fr)_30rem]",
      )}
    >
      {drawer && <div className="absolute inset-0 z-20 bg-black/30 lg:hidden" onClick={() => setDrawer(false)} />}
      <aside
        aria-label="Tables and history"
        className={clsx(
          "min-h-0 overflow-hidden border-r border-line bg-rail",
          drawer ? "absolute inset-y-0 left-0 z-30 w-[18rem] shadow-xl lg:static lg:w-auto lg:shadow-none" : "hidden lg:block",
        )}
      >
        {sidePanel}
      </aside>

      <main className="flex min-h-0 min-w-0 flex-col">
        <header className="flex items-start gap-3 px-5 pt-4 pb-3">
          <Button variant="ghost" className="-ml-2 px-2 lg:hidden" onClick={() => setDrawer(true)} aria-label="Show tables and history">
            <Icon name="menu" size={16} />
          </Button>
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-[23px] leading-tight">SQL Playground</h1>
            <p className="mt-0.5 max-w-[46rem] font-sans text-[13px] text-muted">
              Your own queries, with the same checks and limits as the agent's. Results stay in DataLab, out of the
              agent's reach, until you export them.
            </p>
          </div>
          {chatOpen === "closed" && (
            <Button variant="secondary" className="shrink-0 px-2.5 py-1 text-[12.5px]" onClick={() => setChatOpen("open")}>
              <Icon name="spark" size={13} /> Ask for help
            </Button>
          )}
        </header>

        <div className="px-5">
          <CodeEditor
            label="SQL query"
            language="sql"
            value={sql}
            onChange={setSql}
            diagnostics={diagnostics}
            onSubmit={run}
            extensions={extensions}
            className="h-[clamp(9rem,32vh,22rem)]"
          />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5">
            {runner.running ? (
              <Button key="stop" variant="danger" onClick={() => void runner.stop()} disabled={runner.stopping || runner.starting}>
                <Icon name="stop" size={13} /> {runner.stopping ? "Stopping…" : "Stop"}
              </Button>
            ) : (
              <Button key="run" variant="primary" onClick={run} disabled={!sql.trim()} title={`Run (${submitKeys()})`}>
                <Icon name="db" size={13} /> Run <kbd className="font-sans text-[11.5px] opacity-70">{submitKeys()}</kbd>
              </Button>
            )}
            <CheckSummary sql={sql} check={check} />
          </div>
          {bindNames.length > 0 && (
            <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-2 pb-3">
              <legend className="dl-label mb-1.5">Values for the bind variables</legend>
              {bindNames.map((name) => (
                <label key={name} className="flex items-center gap-2">
                  <span className="font-mono text-[12.5px] text-ink">:{name}</span>
                  <input
                    value={binds[name] ?? ""}
                    onChange={(e) => setBinds({ ...binds, [name]: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
                    }}
                    className="w-40 rounded-[3px] border border-line bg-field px-2 py-1 font-mono text-[12.5px] outline-none focus:border-ink"
                  />
                </label>
              ))}
            </fieldset>
          )}
        </div>

        <div className="flex min-h-0 flex-1 flex-col border-t border-line">
          <ResultsArea
            opened={opened}
            run={runner.run}
            error={runner.error}
            running={runner.running}
            elapsed={elapsed}
            previewRows={previewRows}
            practice={practice}
          />
        </div>
      </main>

      {chatOpen === "open" && (
        <>
          <div className="absolute inset-0 z-20 bg-black/30 xl:hidden" onClick={() => setChatOpen("closed")} />
          <aside
            aria-label="Data extraction chat"
            className="absolute inset-y-0 right-0 z-30 flex w-[min(28rem,100%)] min-h-0 flex-col border-l border-line bg-surface shadow-xl xl:static xl:w-auto xl:shadow-none"
          >
            <DockedChat
              key={chatKey}
              mode="extraction"
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

/** One line under the editor saying what the SQL check makes of the query so far. */
function CheckSummary({ sql, check }: { sql: string; check: SqlCheck | null }) {
  if (!sql.trim()) {
    return (
      <p className="font-sans text-[12.5px] text-muted">
        Write one SELECT query. Name each table with its cohort, as in <code className="font-mono">IHS_2025.TABLE</code>.
      </p>
    );
  }
  if (!check) return <p className="font-sans text-[12.5px] text-faint">Checking…</p>;
  const [error] = check.errors;
  if (error) {
    return (
      <p role="status" className="flex min-w-0 flex-1 items-baseline gap-1.5 font-sans text-[12.5px] text-danger">
        <Icon name="alert" size={13} className="shrink-0 translate-y-[2px]" />
        <span className="min-w-0">
          {where(error.position)}
          {error.message}
        </span>
      </p>
    );
  }
  return (
    <p role="status" className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-1.5 font-sans text-[12.5px] text-muted">
      <span className="flex items-baseline gap-1.5 text-data">
        <Icon name="check" size={13} className="translate-y-[2px]" /> Passes the SQL check
      </span>
      {check.tables.length > 0 && (
        <span>
          · reads <span className="font-mono text-ink">{check.tables.join(", ")}</span>
        </span>
      )}
      {check.warnings.map((w) => (
        <span key={w.message} className="text-attn">
          · {w.message}
        </span>
      ))}
    </p>
  );
}

function where(position: { line: number; column: number } | null | undefined): string {
  return position ? `Line ${position.line}, column ${position.column}: ` : "";
}

function ResultsArea({
  opened,
  run,
  error,
  running,
  elapsed,
  previewRows,
  practice,
}: {
  opened: HistoryItem | null;
  run: SqlRun | null;
  error: string | null;
  running: boolean;
  elapsed: number | null;
  previewRows: number;
  practice: boolean;
}) {
  if (opened) {
    if (opened.has_result) {
      const shown: ShownResult = {
        queryId: opened.query_id,
        rowCount: opened.row_count,
        seconds: opened.elapsed_ms === null ? null : opened.elapsed_ms / 1000,
        tables: opened.tables,
        warnings: [],
      };
      return <ResultGrid key={shown.queryId} result={shown} practice={practice} />;
    }
    return (
      <ResultNote tone={opened.status === "succeeded" ? undefined : "bad"} title={historyTitle(opened.status)}>
        {opened.message}
      </ResultNote>
    );
  }
  if (error) return <ResultNote tone="bad" title="The query couldn't be run.">{error}</ResultNote>;
  if (running || run?.state === "running") {
    return (
      <div role="status" className="flex items-center gap-2.5 px-5 py-5 font-sans text-[13.5px]">
        <span className="size-2 rounded-full bg-ink [animation:dl-breathe_1.6s_ease-in-out_infinite]" />
        Running{elapsed !== null && <span className="text-muted tabular">{formatSeconds(elapsed)}</span>}
      </div>
    );
  }
  if (!run) return <NothingYet previewRows={previewRows} />;
  switch (run.state) {
    case "succeeded": {
      const shown: ShownResult = {
        queryId: run.query_id!,
        rowCount: run.row_count,
        seconds: run.elapsed_seconds,
        tables: run.tables,
        warnings: run.warnings,
      };
      return <ResultGrid key={shown.queryId} result={shown} practice={practice} />;
    }
    case "rejected":
      return (
        <ResultNote tone="bad" title="The SQL check refused this query, so it wasn't run.">
          {where(run.diagnostic?.position)}
          {run.message}
        </ResultNote>
      );
    case "stopped":
      return <ResultNote title="Stopped.">The query was cancelled in the database. No result was kept.</ResultNote>;
    default:
      return (
        <ResultNote tone="bad" title="The query failed.">
          {run.message}
        </ResultNote>
      );
  }
}

function historyTitle(status: string): string {
  if (status === "rejected") return "The SQL check refused this query, so it wasn't run.";
  if (status === "cancelled") return "This query was stopped. No result was kept.";
  if (status === "failed") return "This query failed.";
  if (status === "running") return "This query was still running.";
  return "This result isn't kept any more.";
}

/** The bind variables the query uses, as the check last found them (kept while it checks again). */
function useBindNames(sql: string, check: SqlCheck | null): string[] {
  const [names, setNames] = useState<string[]>([]);
  // Derived while rendering: only a new verdict (or clearing the query) changes them.
  const next = !sql.trim() ? [] : check && !check.errors.length ? check.binds : names;
  if (next.join(",") !== names.join(",")) setNames(next);
  return next;
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
