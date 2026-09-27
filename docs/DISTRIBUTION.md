# Installing, updating, and running DataLab

Status: **draft** for v1. This is a proposal under discussion. Implemented so
far: the installers' first version, and the data side of updating (database
backups, `datalab rollback`, and recovering from an interrupted update; see
"How updating keeps the database safe" below).

Goal: a colleague with no technical background can install DataLab in about
15 minutes, and after that never needs a terminal.

## What ends up on a user's computer

| Piece | What it is | Where it comes from |
|---|---|---|
| Docker Desktop | Runs the sealed agent containers | Docker, installed or checked by the installer |
| DataLab app | The host program: web UI, data service, and orchestration | A GitHub release, with exact pinned versions |
| Agent image | Codex, R, Python, and the curated toolkit | Pulled pre-built from the org's container registry, pinned by digest |
| Gateway image | The small network allowlist and key-injecting gateway | Same registry, pinned by digest |
| Git | Syncs the lab repos | Checked and installed by the installer if missing |
| Lab repos | `ihs-knowledge` and `ihs-pipelines` | Cloned from GitHub into DataLab's data folder |
| Credentials | U-M GPT key, Oracle password, GitHub sign-in | OS keychain (Keychain on Mac, Credential Manager on Windows) |

Nothing is built on the user's machine. The prototype built a 4.6 GB image
locally, which was slow and could differ between machines. Pulling
digest-pinned images means every colleague runs exactly the image that was
tested.

## Installing

The user pastes **one command** into Terminal on Mac or PowerShell on Windows.
It comes from the install page in the app repo. The installer then:

1. **Signs in to GitHub** in the browser, using the "enter this code at
   github.com/login/device" flow. The sign-in is for the private
   knowledge-base and pipelines repos; the app and images are public. If the
   user isn't yet in the `datalab-users` team, the installer says whom to ask
   and finishes everything else.
2. **Checks Docker Desktop.** If it's missing, the installer downloads and
   installs it. This asks for an administrator password on Mac, and on
   Windows it enables WSL2 and may need one restart.
3. **Installs DataLab** from the latest release into a user-level folder,
   using `uv`. `uv` brings its own Python, so there is no Python or Node setup
   to do, and no admin rights are needed for this step. The installer also
   makes sure **Git** is present, which knowledge and pipeline syncing need.
   On Mac, Git comes with Apple's command-line tools. On Windows, the
   installer installs Git for Windows.
4. **Pulls the pinned images** and clones the two lab repos.
5. **Adds a DataLab launcher**: an app in Applications on Mac, and a Start
   menu entry on Windows. The first launch opens **Connections**, which
   asks for the U-M GPT key and Oracle password and saves them to the
   keychain. It then opens **Safety check** so the user can see everything
   pass.

After install, DataLab is started by double-clicking. It opens in the browser
at a local address, and quitting from the app stops everything, including its
containers.

## Updating

- On startup, DataLab checks GitHub for a newer release. If there is one, a
  pill says **Update available**, with the release notes.
- **Update and restart** installs the new version alongside the current one,
  pulls the new images, restarts, and runs the Safety check.
- If anything fails, DataLab stays on the previous version, which is kept.
- **Updates never touch your work files.** Conversations, workspaces, runs,
  and repos are left alone. The one thing an update may change is DataLab's
  database, when a new version needs a new layout. Before that happens:
  - DataLab **backs up the database** to `backups/<version>/`;
  - migrations run only forward, and each one is tested in CI by upgrading a
    real data folder from the previous release;
  - **rolling back** to the previous version restores that backup. Anything
    recorded after the update, such as new conversations, is listed first so
    the user knows what the rollback drops. The files themselves stay on
    disk;
  - if an update is interrupted, for example by a crash or power loss, the
    next start detects the half-finished state and finishes or undoes it
    before opening.

This replaces the prototype's approach of fast-forwarding a git checkout,
which needed seven safety gates. There is no checkout on users' machines to
protect.

### How updating keeps the database safe

This part is built (milestone 7); the updater that calls it, the **Update
available** pill, and the Storage view come later.

**Backups.** Before any migration runs on a database that has data, and
before an update switches versions, DataLab backs up `datalab.sqlite`:

```
backups/0.2.0-20260927-070102-3fa1c2/
  datalab.sqlite    the database just before 0.2.0 changed it
  manifest.json     app_version, from_version, migrations and schema_version,
                    created_at, sha256, size_bytes, reason
```

- The folder is named for the version about to change the database, the
  time, and a random part. Names are never reused, even after older backups are removed.
- The copy uses SQLite's online backup API, so changes still in the
  write-ahead log are included, and it's saved as one self-contained file.
  It's built in a hidden `.incoming-…` folder and renamed into place when
  complete, so a folder with a manifest is always a whole backup.
- If the backup fails (for example, the disk is full), no migration runs and
  DataLab says so instead of starting.
- The newest 3 backups are kept. Backups taken before a rollback are never
  removed automatically and don't count towards the 3, since they may hold
  the only copy of what the rollback dropped. Only folders with a DataLab
  manifest are ever removed; anything else in `backups/` is left alone. A
  folder that can't be removed yet (a file held open by antivirus on Windows,
  say) is left for next time.
- `datalab backup` takes one by hand.

**Rolling back.** A DataLab that finds migrations it doesn't have won't open
the database: a newer version changed it. `datalab rollback` then restores the
newest backup this version can read.

- It only runs when the installed DataLab is older than the database.
- It only restores a backup whose migrations this DataLab has, and whose
  checksum still matches. `--list` shows the backups; `--backup NAME` picks
  one.
- It first lists what the restore drops: rows recorded or changed since the
  backup, per table, naming new conversations. A value in a column the older
  layout doesn't have counts as a change unless it's that column's default.
  If anything would be dropped, it refuses unless given `--yes`.
- **The Data accessed log is never rolled back.** Its rows (`queries`, in
  every layout since the first) are carried over, matched by id, into the
  restored database before it replaces the current one, so the record of what
  was queried always survives a rollback. If they can't be carried over, the
  rollback doesn't happen. Only values in columns the older layout lacks stay
  behind, in the "restore" backup, and they are listed as dropped.
- It backs up the database it replaces (reason "restore"), so a rollback can
  itself be undone. These backups are kept indefinitely; the Storage view
  (still to come) will show them and let the person delete them.
- Upgrading again after a rollback puts each query back under its owner: a
  migration that adds a column the rollback couldn't keep derives it again
  where it can (0006 reads a query's origin from its owner id).
- It holds the data folder's lock (`.lock`, see `datalock.py`) from before it
  works out what to restore until it's done, so it refuses while DataLab is
  running and nothing can be recorded in between. `datalab backup` takes the
  lock too.
- Rolling back to a release from before milestone 7 isn't supported: those
  releases don't have `datalab rollback`, and don't refuse a newer database.

**Only the database is rolled back.** Conversation workspaces, runs, query
results, and repos stay as they are. They are the person's work, and putting
back an older copy would destroy what was done since; they are also far larger
than the database. Rolling back only DataLab's record of them is safe because
each conversation's files live in their own folder (`sessions/<id>/`), which a
newer version adds to but doesn't reorganise. After a rollback, a
conversation made since the update keeps its folder, but the older DataLab no
longer lists it. A rule for releases follows from this: a new version must
not change the layout of files the previous version reads.

**Versions.** Each pre-release has its own version: the package uses PEP 440
(`0.1.0a2`) and the tag the same release (`v0.1.0-alpha.2`), and the release
workflow checks they match. The marker compares versions in either form.

**The update marker.** Whatever runs an update records its progress in
`update-in-progress.json` (`datalab/updates.py`):

1. `begin` once conversations have stopped: writes the marker ("started"),
   backs up the database for the new version, and records the backup and its
   checksum ("backed-up").
2. `advance` to "installed" once the new version is installed beside the old
   one, and to "switched" once the launcher opens it.
3. The new version starts. Its migrations reuse the update's backup if the
   database hasn't changed since, and otherwise take a fresh one. Once it has
   started, the marker is removed, and a line goes in `logs/updates.jsonl`
   (versions and times only).

`begin` and the startup recovery run holding the data folder's lock: the
running DataLab's own, or their own (refusing while a DataLab is running).

If the marker is still there at a start, the update was interrupted:

- **The new version is starting:** it carries on and finishes the update.
- **The old version is starting and the database is unchanged:** the update
  is abandoned, the marker cleared, and the person told they can try again.
- **The old version is starting, but the new one had already migrated the
  database:** if nothing but queries has been recorded since the backup, the
  backup is put back automatically (with the Data accessed log carried over),
  keeping the changed database as a backup. Otherwise, or if the backup isn't
  the update's own or the restore fails, nothing is changed, and DataLab tells
  the person to reopen the newer version or run `datalab rollback`.
- **The marker can't be read:** if the database is one this version can use,
  the marker is set aside (kept for diagnostics) and DataLab starts.

CI checks all of this on every change (`scripts/upgrade-rollback-test.py`): a
data folder made by the previous release's own code is upgraded by the
current code, checked row by row, rolled back (refused first while another
process holds the data folder's lock, and without `--yes`), and checked
again, including that a query recorded after the upgrade survives. It also
fails if a migration in any released tag was changed or removed.

## Where DataLab keeps things

**One data folder** holds everything DataLab owns. On Mac it is
`~/Library/Application Support/DataLab`, and on Windows
`%LOCALAPPDATA%\DataLab`.

```
datalab.sqlite         conversations, workflow runs, settings
backups/<version>/     database backup taken before each update's migration
sessions/<id>/         each conversation's workspace, checkpoints, and query results
runs/<id>/             each workflow run's files and run record
repos/ihs-knowledge/   the lab knowledge base (git)
repos/ihs-pipelines/   the lab pipelines and workflows (git)
logs/                  metadata only, never data
```

**Export destinations** are the only other places DataLab writes. They are
named folders set up in Settings:

- **Exports** (default): `~/Documents/DataLab Exports`.
- **Dropbox**: detected automatically from the Dropbox desktop app's own
  settings file (`info.json`), so the user just picks a subfolder. If
  Dropbox isn't installed, the option says so.

**Nothing is deleted automatically.** Settings has a **Storage** view that
shows disk use per conversation and per run, and offers one-click cleanup of
old ones. This matches the promise "nothing is deleted unless you delete it".
The SQL Playground's results (`<data_dir>/playground/<pg_id>/results/`, one
CSV per query, up to the extraction cap each) are never cleaned up either,
so the Storage view should show them too, with a way to remove old ones.

## Uninstalling

The uninstaller removes the app, the launcher, the images, and the keychain
entries. It asks separately, showing sizes, whether to delete the data folder.
It never touches export destinations. On Windows it also removes what the
installer left: the after-restart logon task or Startup shortcut, its
progress files, and the elevated part's result folder in `%ProgramData%`. It
leaves Docker Desktop, WSL and `docker-users` membership as they are (see
Windows specifics).

## Windows specifics

- Docker Desktop needs WSL2. That means one administrator step and usually a
  restart; the installer resumes where it left off. On Michigan Medicine
  managed machines this may need temporary elevation, the normal JIT process,
  so the installer asks people to request it before they continue.
  - Built (`install.ps1`): the installer asks Windows for permission once, up
    front, and only if something is missing. That one step turns on the WSL
    features, installs WSL and Docker Desktop (pinned versions, checked by
    SHA-256, and Docker's installer by its signature), and adds the person to
    `docker-users` by SID (a name lookup needs the domain controller, which
    isn't reachable off the VPN). Then it offers the restart and opens again
    after sign-in, from a logon task for that person; nothing after this
    needs an administrator.
  - The elevated part never runs anything the person's account could change.
    The elevated window reads a copy of the installer's text once, checks its
    SHA-256 against the text the person's window is running, and runs it from
    memory. It downloads into a new folder, `%ProgramData%\DataLab-setup-<random>`,
    that only SYSTEM and Administrators can use (inheritance off, checked
    before use), checks each download's SHA-256 and Authenticode signature
    (Microsoft for WSL, Docker for Docker Desktop), and runs it from there,
    with `TEMP` pointed inside that folder too. When it's done, it deletes the
    downloads and lets the person read the result and log, and remove the
    folder; the installer removes it after reading it, or on the next run.
  - A Docker Desktop that was already installed, probably without
    `--always-run-service`, gets its service (`com.docker.service`) set to
    start automatically, in the same elevated step. A service IT has
    disabled is left alone.
  - If Windows restarts and the person still can't use Docker (for example,
    a group policy removes them from `docker-users` at each sign-in), the
    installer doesn't ask for another restart: it stops, names the likely
    cause, says to ask IT, and offers to run the elevated part again (never
    with `-Yes`, so it can't restart at every sign-in).
  - Only one installer runs at a time (a named mutex), and each run removes
    the after-restart task and shortcut before anything else; only a run
    that asks for a restart sets them up again.
  - What stays behind, and isn't undone by uninstalling DataLab:
    - `--always-run-service` leaves Docker's service running as SYSTEM all
      the time, starting with Windows. `uninstall.ps1` doesn't touch Docker
      Desktop, WSL, or that service; uninstall Docker Desktop to remove them.
    - Docker Desktop's installer adds the account that runs it to
      `docker-users`. When IT elevates with their own administrator account
      (not temporary rights for the person's own account), that IT account
      is added to `docker-users` on the machine, besides the person.
  - Before starting Docker Desktop, it moves aside socket files an earlier
    Docker left behind, and stops a leftover Docker VM: either stops Docker
    Desktop from starting.
  - uv cuts a `--constraints` path at its first space, and downloads on these
    machines usually sit under `OneDrive - Michigan Medicine`, so the installer
    hands uv a copy of `constraints.txt` under a plain name.
- Credentials go in Windows Credential Manager, and paths use
  `%LOCALAPPDATA%`.
- Windows must be tested on a real managed machine before it's promised to
  colleagues.

## Connectivity

- **Oracle needs the Michigan Medicine VPN.** DataLab's host process does the
  database connections, which avoids the prototype's trouble with Docker
  reaching the VPN. When Oracle is unreachable, the app says "Can't reach the
  database — are you on the VPN?" instead of failing obscurely.
- **U-M GPT** is reached through the gateway container.

## For the maintainer: releasing

- Tagging a release makes GitHub Actions:
  - run all checks, including the upgrade-and-rollback test;
  - build the app with the frontend included;
  - build and push the agent image (the gateway and research proxy are
    upstream images, pinned by digest in the code);
  - publish a release that lists the exact image digests (also in
    `images.json`), with the installers and a `SHA256SUMS` file.
- Not automated yet: signing the Windows scripts and anything macOS runs
  directly (they need the lab's signing identities), and the Windows test on a
  real managed machine. `release.yml` marks each as a TODO.
- Everything is pinned: Python dependencies (`uv.lock`), npm
  (`package-lock.json`), base images, and the Codex CLI version.
- **Diagnostics instead of bug-report uploads.** "Copy diagnostics" in
  Settings produces a metadata-only report: versions, Safety check results,
  and recent errors without content. The user pastes it into an email or a
  GitHub issue. The prototype's feedback flow could zip query results and
  upload them to Dropbox; nothing like that exists in v1.

## Decided

- **Public:** the app repo and its container images. They contain no data or
  secrets. Installing and updating needs no sign-in.
  - Rule: nothing environment-specific goes in the public code. Hostnames,
    service-account names, schema lists, and destinations live in user
    settings or the private lab repos.
- **Private:** `ihs-knowledge` and `ihs-pipelines`. GitHub sign-in is needed
  only for these.
- GitHub sign-in uses a **GitHub App** registered in `SripadaLab-UM`, with
  access limited to the two private repos.
- **Windows is in v1.** It will be tested on a real Windows machine before
  release.
