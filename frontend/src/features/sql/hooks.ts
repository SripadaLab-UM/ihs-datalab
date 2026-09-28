// The Playground's live parts: checking the SQL as it's typed, and following a run.
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type BindValue, type SqlCheck, type SqlRun, sqlApi } from "@/api/sql";
import type { EditorDiagnostic } from "@/components/editor/CodeEditor";

const CHECK_DELAY_MS = 350;

/** The SQL check's verdict on `sql`, asked a moment after typing stops. Only a
 *  verdict on the text as it is now is kept, so marks never land on old text.
 *  Asked again when `catalog` (the catalog's state) changes: it checks every column. */
export function useSqlCheck(
  sql: string,
  catalog?: string,
): { check: SqlCheck | null; diagnostics: EditorDiagnostic[] } {
  const [verdict, setVerdict] = useState<{ sql: string; check: SqlCheck } | null>(null);
  useEffect(() => {
    if (!sql.trim()) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      sqlApi
        .check(sql, controller.signal)
        .then((check) => setVerdict({ sql, check }))
        .catch(() => undefined); // offline or signed out: the run says why
    }, CHECK_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [sql, catalog]);
  const check = sql.trim() && verdict?.sql === sql ? verdict.check : null;
  // A new list only when the verdict changes (the editor re-places marks on each new list).
  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      check
        ? [...check.errors, ...check.warnings].map((d) => ({
            line: d.position?.line ?? 1,
            column: d.position?.column,
            message: d.message,
            severity: d.severity,
          }))
        : [],
    [check],
  );
  return { check, diagnostics };
}

/** Starting, following and stopping one query at a time. */
export function useRun() {
  const [run, setRun] = useState<SqlRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const following = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => () => following.current?.abort(), []);

  const follow = useCallback(
    async (first: SqlRun) => {
      following.current?.abort();
      const controller = new AbortController();
      following.current = controller;
      let current = first;
      try {
        while (current.state === "running") {
          current = await sqlApi.runStatus(current.id, 10, controller.signal);
          if (controller.signal.aborted) return;
          setRun(current);
        }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (!controller.signal.aborted) {
          setStartedAt(null);
          queryClient.invalidateQueries({ queryKey: ["sql-history"] });
        }
      }
    },
    [queryClient],
  );

  const start = useCallback(
    async (sql: string, binds: Record<string, BindValue>) => {
      setError(null);
      setStarting(true);
      try {
        const first = await sqlApi.run(sql, binds);
        setRun(first);
        setStartedAt(Date.now());
        void follow(first);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setStarting(false);
      }
    },
    [follow],
  );

  const stop = useCallback(async () => {
    if (!run || run.state !== "running") return;
    setStopping(true);
    try {
      setRun(await sqlApi.stop(run.id));
    } catch (caught) {
      // It may have finished as Stop was pressed: following it shows how.
      if (!(caught instanceof Error && caught.message.includes("already finished"))) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    } finally {
      setStopping(false);
    }
  }, [run]);

  /** Forget the run shown, such as when a result from history is opened instead. */
  const clear = useCallback(() => {
    following.current?.abort();
    setRun(null);
    setError(null);
    setStartedAt(null);
  }, []);

  const running = starting || run?.state === "running";
  return { run, error, running, starting, stopping, startedAt, start, stop, clear };
}

/** Seconds since `since`, ticking while it's set. */
export function useElapsed(since: number | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [since]);
  return since === null ? null : Math.max(0, (now - since) / 1000);
}

/** A value kept for this browser tab (sessionStorage), so it's still there after visiting another tab. */
export function useTabState<T extends string = string>(key: string, initial: NoInfer<T>): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      return (sessionStorage.getItem(key) as T | null) ?? initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        if (next) sessionStorage.setItem(key, next);
        else sessionStorage.removeItem(key);
      } catch {
        // Storage can be unavailable (a private window): the value lasts until the page goes.
      }
    },
    [key],
  );
  return [value, set];
}
