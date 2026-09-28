// The toolbar's Database and U-M GPT key shortcuts: how each stands, Test
// connection, and a link to its place in Settings → Connections. Secrets are
// never shown: the key's status is where it's saved, nothing of the key itself.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { type Connections, type ConnectionTest, settingsApi } from "@/api/settings";
import { Button, Icon } from "@/components/ui";
import { settingsLink } from "@/features/settings/highlight";

import { PanelHead, Shortcut } from "./Popover";

export const CONNECTIONS = ["settings-connections"];
/** The last Test connection's result in this window, shared by both shortcuts. */
export const CONNECTION_TEST = ["connection-test"];

function useConnections() {
  return useQuery({ queryKey: CONNECTIONS, queryFn: settingsApi.connections });
}

/** Test connection (one test checks both), and its last result. It never runs on its own. */
function useConnectionTest() {
  const client = useQueryClient();
  const test = useQuery({
    queryKey: CONNECTION_TEST,
    queryFn: settingsApi.testConnections,
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });
  return {
    result: test.data,
    running: test.isFetching,
    error: test.error,
    run: () => client.fetchQuery({ queryKey: CONNECTION_TEST, queryFn: settingsApi.testConnections, staleTime: 0 }).catch(() => {}),
  };
}

type Tone = "good" | "attn" | undefined;
interface Standing {
  /** Words for the state ("connected", "password missing"). */
  state: string;
  tone: Tone;
  /** Beside the icon, when it needs attention. */
  attention?: string;
}

export function databaseStanding(connections: Connections | undefined, result?: ConnectionTest["database"]): Standing {
  if (!connections) return { state: "checking…", tone: undefined };
  const oracle = connections.oracle;
  if (result && !result.ok) return { state: "test failed", tone: "attn", attention: "DB: not connected" };
  if (oracle.practice) {
    return { state: result?.ok ? "connected (synthetic)" : "practice: synthetic database", tone: result?.ok ? "good" : undefined };
  }
  if (!oracle.configured) return { state: "not set up", tone: "attn", attention: "DB: not set up" };
  if (oracle.password === "missing") return { state: "password missing", tone: "attn", attention: "DB: no password" };
  if (result?.ok) return { state: "connected", tone: "good" };
  return { state: "set up, not tested in this window", tone: undefined };
}

export function keyStanding(connections: Connections | undefined, result?: ConnectionTest["model"]): Standing {
  if (!connections) return { state: "checking…", tone: undefined };
  const source = connections.model.key;
  if (source === "missing") return { state: "not saved", tone: "attn", attention: "Key missing" };
  if (result && !result.ok) return { state: "test failed", tone: "attn", attention: "Key: not working" };
  const where = source === "keychain" ? "saved in keychain" : "set by an environment variable";
  return { state: result?.ok ? `${where}, working` : where, tone: result?.ok ? "good" : undefined };
}

function Outcome({ ok, message }: { ok: boolean; message: string }) {
  return (
    <p role="status" className={`mt-3 flex items-start gap-1.5 text-[12.5px] ${ok ? "text-data" : "text-danger"}`}>
      <Icon name={ok ? "check" : "alert"} size={13} className="mt-px shrink-0" />
      <span>{message}</span>
    </p>
  );
}

const ACTIONS = "mt-4 flex flex-wrap items-center gap-2";
const LINK =
  "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-[3px] border border-line px-3 py-1.5 font-sans text-[13.5px] font-medium text-ink transition-colors hover:border-ink";

export function DatabaseShortcut() {
  const connections = useConnections();
  const test = useConnectionTest();
  const shown = connections.data;
  const result = test.result?.database;
  const standing = databaseStanding(shown, result);
  const practice = shown?.oracle.practice;
  return (
    <Shortcut
      icon="db"
      label={`Database: ${standing.state}`}
      title={`Database: ${standing.state} · click to test or open settings`}
      attention={standing.attention}
    >
      {(close) => (
        <>
          <PanelHead title="The study database" state={standing.state} tone={standing.tone} />
          {connections.isError && <p className="mt-2 text-[12.5px] text-danger">{connections.error.message}</p>}
          {shown && (
            <p className="mt-2 text-[12.5px] text-muted">
              {practice ? (
                "Practice: synthetic database (fixed)"
              ) : shown.oracle.configured ? (
                <>
                  <span className="font-mono text-[11.5px] break-all">{shown.oracle.dsn}</span>
                  <span className="block">as {shown.oracle.user}, read-only</span>
                </>
              ) : (
                "No database is set up yet. Ask the DataLab maintainer for the lab's settings file."
              )}
            </p>
          )}
          {result && <Outcome ok={result.ok} message={result.message} />}
          {test.error && <Outcome ok={false} message={test.error.message} />}
          <div className={ACTIONS}>
            {shown?.oracle.configured && (
              <Button onClick={test.run} disabled={test.running}>
                {test.running ? "Testing…" : "Test connection"}
              </Button>
            )}
            <Link {...settingsLink("connections", "connection-database")} onClick={() => close(false)} className={LINK}>
              Open connection settings
            </Link>
          </div>
        </>
      )}
    </Shortcut>
  );
}

export function KeyShortcut() {
  const connections = useConnections();
  const test = useConnectionTest();
  const shown = connections.data;
  const result = test.result?.model;
  const standing = keyStanding(shown, result);
  const canSet = shown?.model.can_set_key;
  const missing = shown?.model.key === "missing";
  return (
    <Shortcut
      icon="key"
      label={`U-M GPT key: ${standing.state}`}
      title={`U-M GPT key: ${standing.state} · click to ${canSet ? (missing ? "add the key" : "replace the key") + " or test the connection" : "test the connection"}`}
      attention={standing.attention}
    >
      {(close) => (
        <>
          <PanelHead title="U-M GPT key" state={standing.state} tone={standing.tone} />
          {connections.isError && <p className="mt-2 text-[12.5px] text-danger">{connections.error.message}</p>}
          {shown && (
            <p className="mt-2 text-[12.5px] text-muted">
              {shown.model.key === "keychain"
                ? "Saved in keychain. DataLab never shows it again."
                : shown.model.key === "environment"
                  ? "Set by an environment variable (development only)."
                  : canSet
                    ? "No key saved yet. Add your U-M GPT (Toolkit) API key to use the Workspace."
                    : "No key saved. Practice uses the key the real DataLab saved, or one saved with datalab setup --profile practice --update."}
            </p>
          )}
          {result && <Outcome ok={result.ok} message={result.message} />}
          {test.error && <Outcome ok={false} message={test.error.message} />}
          <div className={ACTIONS}>
            {!missing && shown && (
              <Button onClick={test.run} disabled={test.running}>
                {test.running ? "Testing…" : "Test connection"}
              </Button>
            )}
            <Link {...settingsLink("connections", "connection-umgpt")} onClick={() => close(false)} className={LINK}>
              {canSet ? (missing ? "Add key…" : "Replace key…") : "Open connection settings"}
            </Link>
          </div>
        </>
      )}
    </Shortcut>
  );
}
