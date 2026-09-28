import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";

import { type Connections, type ConnectionTest, settingsApi } from "@/api/settings";
import { Button, Chip, Icon, InfoTip } from "@/components/ui";

import { GitHubSection } from "./GitHubSection";
import { FixedOnPractice, Part, Section } from "./Section";

type Source = Connections["model"]["key"];

/**
 * Connections: U-M GPT, the study database and GitHub, each with how it
 * stands and what can be done about it. The key and the database password
 * live in this computer's keychain; they can be saved or replaced here, never
 * read back. The practice profile's database is fixed, and says so.
 */
export function ConnectionsSection() {
  const connections = useQuery({ queryKey: ["settings-connections"], queryFn: settingsApi.connections });
  // One test checks both, so either part's button shows both results.
  const test = useMutation({ mutationFn: settingsApi.testConnections });
  const shown = connections.data;
  const testButton = (
    <Button onClick={() => test.mutate()} disabled={test.isPending || !shown}>
      {test.isPending ? "Testing…" : "Test connection"}
    </Button>
  );

  return (
    <Section title="Connections">
      <p className="mt-1 text-sm text-muted">
        DataLab keeps the database password and the U-M GPT key in this computer's keychain. You can save or replace
        them here; DataLab never shows them again.
      </p>
      {connections.isError && <p className="mt-3 text-sm text-danger">{connections.error.message}</p>}
      {test.isError && <p className="mt-3 text-sm text-danger">{test.error.message}</p>}
      {shown && (
        <div className="mt-6 flex flex-col gap-8">
          {shown.read_only_because && (
            <p className="border-l-2 border-attn/50 pl-3 text-sm text-muted">{shown.read_only_because}</p>
          )}
          <Model connections={shown} result={test.data?.model} testButton={testButton} />
          <Database connections={shown} result={test.data?.database} testButton={testButton} />
          <GitHubSection />
          <dl className="grid grid-cols-[10rem_1fr] gap-y-1.5 border-t border-line pt-5 text-sm">
            <dt className="text-muted">Profile</dt>
            <dd>{shown.profile === "practice" ? "Practice (synthetic data only)" : "Real data"}</dd>
            <dt className="text-muted">Settings file</dt>
            <dd className="min-w-0 truncate font-mono text-xs leading-5" title={shown.settings_file}>
              {shown.settings_file}
            </dd>
          </dl>
        </div>
      )}
    </Section>
  );
}

function Model({
  connections,
  result,
  testButton,
}: {
  connections: Connections;
  result?: ConnectionTest["model"];
  testButton: ReactNode;
}) {
  const model = connections.model;
  const practice = connections.profile === "practice";
  return (
    <SecretPart
      id="connection-umgpt"
      title="U-M GPT"
      source={model.key}
      what="key"
      canSet={model.can_set_key}
      fixed={practice && !model.can_set_key}
      save={settingsApi.saveModelKey}
      label="U-M GPT API key"
      testButton={testButton}
    >
      <dl className="mt-3 grid grid-cols-[10rem_1fr] items-baseline gap-y-1.5 text-sm">
        <dt className="text-muted">Service</dt>
        <dd className="font-mono text-xs">{model.base_url}</dd>
        <dt className="text-muted">API key</dt>
        <dd>
          {practice && !model.can_set_key
            ? "The key the real DataLab saved, or one saved with datalab setup --profile practice --update."
            : "Your U-M GPT (Toolkit) API key."}
        </dd>
      </dl>
      {result && <Outcome ok={result.ok} message={result.message} />}
    </SecretPart>
  );
}

function Database({
  connections,
  result,
  testButton,
}: {
  connections: Connections;
  result?: ConnectionTest["database"];
  testButton: ReactNode;
}) {
  const oracle = connections.oracle;
  const roles = result?.ok && result.enabled_roles.length ? `Enabled roles: ${result.enabled_roles.join(", ")}` : "";
  const details = !oracle.configured ? (
    <p className="mt-2 text-sm text-muted">
      No database is set up yet. Ask the DataLab maintainer for the lab's settings file, then run{" "}
      <code className="font-mono text-xs">datalab setup --settings &lt;file&gt;</code>.
    </p>
  ) : (
    <dl className="mt-3 grid grid-cols-[10rem_1fr] items-baseline gap-y-1.5 text-sm">
      <dt className="text-muted">Database</dt>
      <dd className="font-mono text-xs">
        {oracle.dsn}
        {oracle.practice && <span className="ml-2 font-sans text-muted">synthetic, on this computer</span>}
      </dd>
      <dt className="text-muted">Account</dt>
      <dd className="font-mono text-xs">{oracle.user}</dd>
      <dt className="flex items-center gap-1 text-muted">
        Read-only roles <InfoTip term="read-only" />
      </dt>
      <dd className="flex flex-wrap gap-1">
        {oracle.read_only_roles.length ? (
          oracle.read_only_roles.map((role) => <Chip key={role}>{role}</Chip>)
        ) : (
          <span className="text-muted">none</span>
        )}
        <span className="w-full text-xs text-muted">
          The only roles DataLab switches on for each connection, so the session can read and nothing else.
        </span>
      </dd>
      <dt className="text-muted">Cohorts</dt>
      <dd className="flex flex-wrap gap-1">
        {oracle.allowed_schemas.map((schema) => (
          <Chip key={schema}>{schema}</Chip>
        ))}
      </dd>
      {oracle.password === null && (
        <>
          <dt className="text-muted">Password</dt>
          <dd className="text-muted">Fixed: the synthetic database's own</dd>
        </>
      )}
    </dl>
  );
  const outcome = result && <Outcome ok={result.ok} message={result.message} extra={roles} />;

  if (!oracle.configured || oracle.password === null) {
    // Nothing to save: not set up yet, or practice's own synthetic database.
    return (
      <Part
        id="connection-database"
        title="The study database"
        state={
          oracle.practice ? (
            <FixedOnPractice>Fixed on the practice DataLab: the synthetic database</FixedOnPractice>
          ) : (
            !oracle.configured && <Chip tone="attn">Not set up</Chip>
          )
        }
        actions={oracle.configured && testButton}
      >
        {details}
        {outcome}
      </Part>
    );
  }
  return (
    <SecretPart
      id="connection-database"
      title="The study database"
      source={oracle.password}
      what="password"
      canSet={oracle.can_set_password}
      fixed={false}
      save={settingsApi.saveDatabasePassword}
      label={`Database password for ${oracle.user}`}
      testButton={testButton}
    >
      {details}
      {outcome}
    </SecretPart>
  );
}

const SOURCE: Record<Source, ReactNode> = {
  keychain: <Chip tone="good">Saved in keychain</Chip>,
  environment: <Chip tone="attn">Set by an environment variable (development only)</Chip>,
  missing: <Chip tone="attn">Not saved</Chip>,
};

/**
 * A connection with a secret: whether it's saved, Save or Replace (a
 * write-only field), and Test connection, on the title's line.
 */
function SecretPart({
  id,
  title,
  source,
  what,
  canSet,
  fixed,
  save,
  label,
  testButton,
  children,
}: {
  id: string;
  title: string;
  source: Source;
  what: "password" | "key";
  canSet: boolean;
  fixed: boolean;
  save: (value: string) => Promise<void>;
  label: string;
  testButton: ReactNode;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const mutation = useMutation({
    mutationFn: save,
    onSuccess: () => {
      setValue("");
      setEditing(false);
      setSaved(true);
      mutation.reset();
      queryClient.invalidateQueries({ queryKey: ["settings-connections"] });
    },
  });
  const overridden = source === "environment" && canSet && (
    <span className="text-xs text-attn">
      An environment variable sets this {what}, so DataLab uses that one: saving here won't take effect until
      it's removed.
    </span>
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value) mutation.mutate(value);
  };
  const cancel = () => {
    setValue("");
    setEditing(false);
    mutation.reset();
  };

  return (
    <Part
      id={id}
      title={title}
      state={
        <>
          {SOURCE[source]}
          {fixed && <FixedOnPractice />}
          {saved && (
            <span className="inline-flex items-center gap-1 text-sm text-data" role="status">
              <Icon name="check" size={13} /> Saved
            </span>
          )}
        </>
      }
      actions={
        <>
          {canSet && !editing && (
            <Button
              onClick={() => {
                setSaved(false);
                setEditing(true);
              }}
            >
              {source === "missing" ? `Save ${what}` : `Replace ${what}`}
            </Button>
          )}
          {testButton}
        </>
      }
    >
      {overridden && !editing && <p className="mt-2">{overridden}</p>}
      {editing && (
        <form onSubmit={submit} className="mt-3 flex flex-col gap-2 border-l border-you pl-4">
          <span className="flex flex-wrap items-center gap-2">
            <input
              type="password"
              aria-label={label}
              autoComplete="new-password"
              spellCheck={false}
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="w-72 max-w-full rounded-[3px] border border-line bg-field px-2 py-1 font-mono text-xs outline-none focus:border-ink"
            />
            <Button type="submit" variant="primary" disabled={!value || mutation.isPending}>
              {mutation.isPending ? "Saving…" : "Save to keychain"}
            </Button>
            <Button type="button" onClick={cancel}>
              Cancel
            </Button>
          </span>
          {overridden}
          <span className="text-xs text-muted">
            It goes straight to this computer's keychain. DataLab never shows it again, here or anywhere.
          </span>
          {mutation.isError && <span className="text-xs text-danger">{mutation.error.message}</span>}
        </form>
      )}
      {children}
    </Part>
  );
}

function Outcome({ ok, message, extra }: { ok: boolean; message: string; extra?: string }) {
  return (
    <p className={`mt-3 flex items-start gap-2 text-sm ${ok ? "text-data" : "text-danger"}`} role="status">
      <Icon name={ok ? "check" : "alert"} size={14} className="mt-0.5 shrink-0" />
      <span>
        {message}
        {extra && <span className="block text-xs text-muted">{extra}</span>}
      </span>
    </p>
  );
}
