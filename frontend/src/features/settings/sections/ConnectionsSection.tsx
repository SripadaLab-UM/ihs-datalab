import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";

import { type Connections, type ConnectionTest, settingsApi } from "@/api/settings";
import { Button, Chip, Icon, InfoTip } from "@/components/ui";

import { Section } from "./Section";

type Source = Connections["model"]["key"];

/**
 * Connections: the study database and U-M GPT. The database password and the
 * key live in this computer's keychain; they can be saved or replaced here,
 * never read back. The practice profile only shows its synthetic database.
 */
export function ConnectionsSection() {
  const connections = useQuery({ queryKey: ["settings-connections"], queryFn: settingsApi.connections });
  const test = useMutation({ mutationFn: settingsApi.testConnections });
  const shown = connections.data;

  return (
    <Section
      title="Connections"
      actions={
        <Button onClick={() => test.mutate()} disabled={test.isPending || !shown}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
      }
    >
      <p className="mt-1 text-sm text-muted">
        DataLab keeps the database password and the U-M GPT key in this computer's keychain. You can save or replace
        them here; DataLab never shows them again.
      </p>
      {connections.isError && <p className="mt-3 text-sm text-danger">{connections.error.message}</p>}
      {test.isError && <p className="mt-3 text-sm text-danger">{test.error.message}</p>}
      {shown && (
        <>
          {shown.read_only_because && (
            <p className="mt-3 border-l-2 border-line pl-3 text-sm text-muted">{shown.read_only_because}</p>
          )}
          <Database connections={shown} result={test.data?.database} />
          <Model connections={shown} result={test.data?.model} />
          <dl className="mt-6 grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
            <dt className="text-muted">Profile</dt>
            <dd>{shown.profile === "practice" ? "Practice (synthetic data only)" : "Real data"}</dd>
            <dt className="text-muted">Settings file</dt>
            <dd className="min-w-0 truncate font-mono text-xs leading-5" title={shown.settings_file}>
              {shown.settings_file}
            </dd>
          </dl>
        </>
      )}
    </Section>
  );
}

function Database({ connections, result }: { connections: Connections; result?: ConnectionTest["database"] }) {
  const oracle = connections.oracle;
  const roles = result?.ok && result.enabled_roles.length ? `Enabled roles: ${result.enabled_roles.join(", ")}` : "";
  return (
    <div className="mt-6">
      <h3 className="dl-label">The study database</h3>
      {!oracle.configured ? (
        <p className="mt-2 text-sm text-muted">
          No database is set up yet. Ask the DataLab maintainer for the lab's settings file, then run{" "}
          <code className="font-mono text-xs">datalab setup --settings &lt;file&gt;</code>.
        </p>
      ) : (
        <dl className="mt-2 grid grid-cols-[10rem_1fr] items-baseline gap-y-1.5 text-sm">
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
          <dt className="text-muted">Password</dt>
          <dd>
            {oracle.password === null ? (
              <span className="text-muted">Fixed: the synthetic database's own</span>
            ) : (
              <SecretLine
                source={oracle.password}
                what="password"
                canSet={oracle.can_set_password}
                save={settingsApi.saveDatabasePassword}
                label={`Database password for ${oracle.user}`}
              />
            )}
          </dd>
        </dl>
      )}
      {result && <Outcome ok={result.ok} message={result.message} extra={roles} />}
    </div>
  );
}

function Model({ connections, result }: { connections: Connections; result?: ConnectionTest["model"] }) {
  const model = connections.model;
  return (
    <div className="mt-6">
      <h3 className="dl-label">U-M GPT</h3>
      <dl className="mt-2 grid grid-cols-[10rem_1fr] items-baseline gap-y-1.5 text-sm">
        <dt className="text-muted">Service</dt>
        <dd className="font-mono text-xs">{model.base_url}</dd>
        <dt className="text-muted">API key</dt>
        <dd>
          <SecretLine
            source={model.key}
            what="key"
            canSet={model.can_set_key}
            save={settingsApi.saveModelKey}
            label="U-M GPT API key"
          />
        </dd>
      </dl>
      {result && <Outcome ok={result.ok} message={result.message} />}
    </div>
  );
}

const SOURCE: Record<Source, ReactNode> = {
  keychain: "Saved in the keychain",
  environment: "Set by an environment variable (development only)",
  missing: <span className="text-attn">Not saved yet</span>,
};

/** Whether a secret is saved, and a write-only field to save or replace it. */
function SecretLine({
  source,
  what,
  canSet,
  save,
  label,
}: {
  source: Source;
  what: "password" | "key";
  canSet: boolean;
  save: (value: string) => Promise<void>;
  label: string;
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

  if (!editing) {
    return (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {overridden && <span className="w-full">{overridden}</span>}
        <span>{SOURCE[source]}</span>
        {saved && (
          <span className="inline-flex items-center gap-1 text-data" role="status">
            <Icon name="check" size={13} /> Saved
          </span>
        )}
        {canSet && (
          <Button
            variant="ghost"
            className="px-1 py-0 text-xs underline decoration-faint underline-offset-4"
            onClick={() => {
              setSaved(false);
              setEditing(true);
            }}
          >
            {source === "missing" ? `Save the ${what}…` : `Replace the ${what}…`}
          </Button>
        )}
      </span>
    );
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <span className="flex flex-wrap items-center gap-2">
        <input
          type="password"
          aria-label={label}
          autoComplete="new-password"
          spellCheck={false}
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-72 rounded-[3px] border border-line bg-field px-2 py-1 font-mono text-xs outline-none focus:border-ink"
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
