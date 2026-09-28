import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { formatBytes, settingsApi, type UpdateCheck, type UpdateRelease, type Updates } from "@/api/settings";
import { Button, Chip, Icon, Modal } from "@/components/ui";

import { settingsPath } from "../sectionIds";
import { installing, UPDATE_CHECK, useUpdateCheck } from "../updateCheck";
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

const STEP: Record<string, string> = {
  downloading: "Downloading and checking the new version",
  stopping: "Stopping conversations",
  "backing-up": "Backing up the database",
  installing: "Installing it beside this version",
  "pulling-images": "Downloading its container images",
  switching: "Switching the launcher to it",
  restarting: "Restarting",
};

/**
 * Updates: the installed version, what the last check for a newer release
 * found (with its notes and Install update, always confirmed), an update in
 * progress or interrupted, what DataLab did about it at this start, and the
 * database backups.
 */
export function UpdatesSection() {
  const updates = useQuery({ queryKey: ["settings-updates"], queryFn: settingsApi.updates });
  const shown = updates.data;
  return (
    <div id="updates" className="scroll-mt-6">
      <Section title="Updates">
        {updates.isError && <p className="mt-3 text-sm text-danger">{updates.error.message}</p>}
        {shown && <Status updates={shown} />}
      </Section>
    </div>
  );
}

/** What the last check found, Check now, and the release on offer. */
function NewVersions({ initial }: { initial: UpdateCheck }) {
  const queryClient = useQueryClient();
  const live = useUpdateCheck();
  const check = live.data ?? initial;
  const [confirming, setConfirming] = useState(false);
  const checkNow = useMutation({
    mutationFn: settingsApi.checkForUpdates,
    onSuccess: (next) => queryClient.setQueryData(UPDATE_CHECK, next),
  });
  const working = installing(check);
  const failed = check.install.state === "failed";
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center gap-3 rounded-[3px] border border-line px-4 py-3">
        <p className="min-w-0 flex-1 text-[15px]" role="status">
          {check.message}
          {check.checked_at && <span className="block text-xs text-muted">Last checked {when(check.checked_at)}</span>}
        </p>
        {check.state !== "not-configured" && (
          <Button variant="primary" onClick={() => checkNow.mutate()} disabled={checkNow.isPending || working}>
            {checkNow.isPending ? "Checking…" : "Check now"}
          </Button>
        )}
      </div>
      {checkNow.error && <p className="mt-2 text-sm text-danger">{checkNow.error.message}</p>}

      {working && (
        <p className="mt-4 flex items-start gap-2 border-l-2 border-attn pl-3 text-sm" role="status">
          <Icon name="restore" size={14} className="mt-0.5 shrink-0" />
          <span>
            <span className="block font-medium">
              {STEP[check.install.state] ?? check.install.state} (DataLab {check.install.version})
            </span>
            {check.install.message}
          </span>
        </p>
      )}
      {failed && (
        <p className="mt-4 border-l-2 border-danger pl-3 text-sm text-danger" role="alert">
          <span className="block font-medium">The update didn't happen</span>
          {check.install.message}
        </p>
      )}

      {check.available && !working && (
        <Offer
          release={check.available}
          current={check.current_version}
          canInstall={check.can_install}
          why={check.cannot_install_because}
          onInstall={() => setConfirming(true)}
        />
      )}
      {confirming && check.available && (
        <ConfirmInstall release={check.available} current={check.current_version} onClose={() => setConfirming(false)} />
      )}
    </div>
  );
}

function Offer({
  release,
  current,
  canInstall,
  why,
  onInstall,
}: {
  release: UpdateRelease;
  current: string;
  canInstall: boolean;
  why: string | null;
  onInstall: () => void;
}) {
  return (
    <div className="mt-4 rounded-[3px] border border-you/40 bg-you-soft/40 p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="font-serif text-[19px] leading-tight">{release.title}</h3>
          <p className="mt-0.5 text-xs text-muted">
            DataLab {release.version}
            {release.prerelease && " · pre-release"}
            {release.published_at && ` · published ${when(release.published_at)}`} · you have {current}
            {release.page && (
              <>
                {" · "}
                <a href={release.page} target="_blank" rel="noreferrer noopener" className="underline hover:text-ink">
                  on GitHub
                </a>
              </>
            )}
          </p>
        </div>
        <Button variant="primary" onClick={onInstall} disabled={!canInstall}>
          Install update…
        </Button>
      </div>
      {why && <p className="mt-2 text-xs text-muted">{why}</p>}
      <h4 className="dl-label mt-3">Release notes</h4>
      {/* As written on GitHub, shown as text: never rendered as HTML. */}
      <pre className="mt-1 max-h-64 overflow-auto font-sans text-sm whitespace-pre-wrap">
        {release.notes.trim() || "No notes for this release."}
      </pre>
    </div>
  );
}

function ConfirmInstall({ release, current, onClose }: { release: UpdateRelease; current: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const install = useMutation({
    mutationFn: () => settingsApi.installUpdate(release.version),
    onSuccess: (next) => {
      queryClient.setQueryData(UPDATE_CHECK, next);
      onClose();
    },
  });
  return (
    <Modal title={`Install DataLab ${release.version}?`} onClose={onClose}>
      <div className="flex flex-col gap-3 text-sm">
        <p>Installing the update:</p>
        <ol className="ml-5 list-decimal space-y-1">
          <li>closes the conversations' sandboxes (nothing may be working; conversations reopen after the restart);</li>
          <li>backs up DataLab's database;</li>
          <li>
            installs DataLab {release.version} beside {current}, which is kept, so you can go back to it;
          </li>
          <li>restarts DataLab. It opens again in a new window; run the Safety check there.</li>
        </ol>
        <p className="text-muted">
          Your files (conversations' workspaces, query results, exports and the lab's repos) aren't touched. If
          anything fails, DataLab stays on {current}.
        </p>
        {install.error && <p className="text-danger">{install.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => install.mutate()} disabled={install.isPending}>
            {install.isPending ? "Starting…" : "Install and restart"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Status({ updates }: { updates: Updates }) {
  const recovery = updates.recovery;
  const needsYou = recovery?.outcome === "needs-you";
  return (
    <>
      <NewVersions initial={updates.check} />

      <dl className="mt-5 grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
        <dt className="text-muted">Installed</dt>
        <dd className="font-mono text-xs leading-5">DataLab {updates.version}</dd>
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
            {recovery.outcome === "finishing" && (
              <span className="block">
                Run the{" "}
                <Link to={settingsPath("safety")} className="underline decoration-faint underline-offset-4 hover:decoration-ink">
                  Safety check
                </Link>{" "}
                to see everything still holds.
              </span>
            )}
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
          diagnostics (in About) and send them to the DataLab maintainer.
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
          back to one this version can read. Remove old ones in{" "}
          <Link to={settingsPath("storage")} className="underline hover:text-ink">
            Storage
          </Link>
          .
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
