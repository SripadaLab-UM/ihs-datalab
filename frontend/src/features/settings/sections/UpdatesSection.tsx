import { useQuery } from "@tanstack/react-query";

import { formatBytes, settingsApi, type Updates } from "@/api/settings";
import { Chip, Icon } from "@/components/ui";

import { Section } from "./Section";

const STATE: Record<string, string> = {
  started: "started",
  "backed-up": "database backed up",
  installed: "new version installed",
  switched: "switched to the new version",
};

const OUTCOME: Record<string, string> = {
  finished: "Updated",
  abandoned: "Didn't finish; nothing changed",
  undone: "Interrupted; the database was put back",
  unreadable: "Left a note DataLab couldn't read",
};

const REASON: Record<string, string> = {
  migrate: "before an upgrade",
  update: "before an update",
  restore: "before a rollback",
  manual: "taken by hand",
};

/**
 * Updates: the installed version, an update in progress or interrupted, what
 * DataLab did about it at this start, and the database backups. Checking for
 * a newer release isn't built yet.
 */
export function UpdatesSection() {
  const updates = useQuery({ queryKey: ["settings-updates"], queryFn: settingsApi.updates });
  const shown = updates.data;
  return (
    <Section title="Updates">
      {updates.isError && <p className="mt-3 text-sm text-danger">{updates.error.message}</p>}
      {shown && <Status updates={shown} />}
    </Section>
  );
}

function Status({ updates }: { updates: Updates }) {
  const recovery = updates.recovery;
  const needsYou = recovery?.outcome === "needs-you";
  return (
    <>
      <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
        <dt className="text-muted">Installed</dt>
        <dd className="font-mono text-xs leading-5">DataLab {updates.version}</dd>
        <dt className="text-muted">New versions</dt>
        <dd className="text-muted">{updates.check_message}</dd>
        <dt className="text-muted">Database layout</dt>
        <dd className="font-mono text-xs leading-5">
          {updates.latest_migration ?? "empty"}{" "}
          <span className="font-sans text-muted">({updates.migrations_applied} changes applied)</span>
        </dd>
      </dl>

      {recovery && (
        <p
          className={`mt-4 flex items-start gap-2 border-l-2 pl-3 text-sm ${needsYou ? "border-you text-you" : "border-line"}`}
          role="status"
        >
          <Icon name={needsYou ? "alert" : "restore"} size={14} className="mt-0.5 shrink-0" />
          <span>
            {needsYou && <span className="block font-medium">This needs you</span>}
            {recovery.message}
          </span>
        </p>
      )}
      {updates.marker && (
        <p className="mt-4 border-l-2 border-attn pl-3 text-sm">
          An update from {updates.marker.from_version} to {updates.marker.to_version} is under way (
          {STATE[updates.marker.state] ?? updates.marker.state}, since {when(updates.marker.started_at)}).
        </p>
      )}
      {updates.marker_unreadable && (
        <p className="mt-4 border-l-2 border-attn pl-3 text-sm text-attn">
          An update left a note DataLab can't read. Start DataLab again to sort it out; if it stays, copy the
          diagnostics below and send them to the DataLab maintainer.
        </p>
      )}

      {updates.history.length > 0 && (
        <div className="mt-6">
          <h3 className="dl-label">Recent updates</h3>
          <ul className="mt-1">
            {updates.history.map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="flex gap-4 border-b border-line py-1.5 text-sm last:border-b-0">
                <span className="w-44 shrink-0 text-muted">{when(entry.at)}</span>
                <span className="flex-1">
                  {OUTCOME[entry.outcome] ?? entry.outcome}
                  {entry.from_version && entry.to_version && (
                    <span className="font-mono text-xs text-muted">
                      {" "}
                      {entry.from_version} → {entry.to_version}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6">
        <h3 className="dl-label">Database backups</h3>
        <p className="mt-1 text-xs text-muted">
          Taken before an update changes the database. <code className="font-mono">datalab rollback</code> can go
          back to one this version can read. Remove old ones in Storage, above.
        </p>
        {updates.backups.length === 0 ? (
          <p className="mt-2 text-sm text-muted">No backups yet. The first is taken before an update changes the database.</p>
        ) : (
          <ul className="mt-1">
            {updates.backups.map((backup) => (
              <li key={backup.name} className="flex items-center gap-3 border-b border-line py-1.5 text-sm last:border-b-0">
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-xs" title={backup.name}>
                    {backup.name}
                  </span>
                  <span className="text-xs text-muted">
                    {when(backup.created_at)} · {REASON[backup.reason] ?? backup.reason} · DataLab {backup.app_version}
                  </span>
                </span>
                {backup.kept && <Chip tone="attn">kept for good</Chip>}
                {!backup.usable && <Chip title="It has changes this DataLab doesn't know about.">for a newer DataLab</Chip>}
                <span className="tabular shrink-0 font-mono text-xs">{formatBytes(backup.size_bytes)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
