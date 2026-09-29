# Installing, updating, and running DataLab

Status: **implemented**: the installers (versions side by side, Docker
Desktop, the GitHub steps, the practice database), the update check (at
start and about once an hour), **Update available** and the in-app updater
of signed releases, and the data side of updating (database backups,
`datalab rollback`, recovering from an interrupted update). Tried on a Mac;
on Windows the updater was tried once and the current installer still needs
a re-test (see "Windows specifics"). Readiness is in
[PRODUCT.md](../PRODUCT.md).

Goal: a colleague with no technical background can install DataLab in about
15 minutes, and after that never needs a terminal.

## What ends up on a user's computer

| Piece | What it is | Where it comes from |
|---|---|---|
| Docker Desktop | Runs the sealed agent containers | Docker, installed or checked by the installer |
| DataLab app | The host program: web UI, data service, and orchestration. One folder per version, side by side | A GitHub release, with exact pinned versions |
| Agent image | Codex, R, Python, and the curated toolkit | Pulled pre-built from the org's container registry, pinned by digest |
| Gateway and research proxy images | Stock nginx (routes a session's traffic; holds no key) and Squid (research sessions' internet) | Upstream images, pinned by digest in `sessions/containers.py` |
| Oracle Database Free image | The practice DataLab's synthetic database | Oracle's registry, pinned by digest in `practice_db` (practice only) |
| Git | Syncs the lab repos | Checked and installed by the installer if missing |
| Lab repos | `ihs-knowledge` and `ihs-pipelines` | Cloned from GitHub into DataLab's data folder |
| Credentials | U-M GPT key, Oracle password, GitHub sign-in | OS keychain (Keychain on Mac, Credential Manager on Windows) |

Nothing is built on the user's machine. The prototype built a 4.6 GB image
locally, which was slow and could differ between machines. Pulling
digest-pinned images means every colleague runs exactly the image that was
tested.

## Installing

The user pastes **one command** into Terminal on Mac or PowerShell on Windows.
It comes from the lab's install page (maintained outside this repo), which
includes the lab's settings, so no settings file is needed. The manual path,
from a release's files with `--settings <file>` (`-Settings` on Windows), is
in the README. The installer then:

1. **Checks Docker Desktop.** If it's missing, the installer offers to
   download and install it, and starts it if it isn't running. On Mac the
   person accepts Docker's agreement in Docker's own first-run window (see
   "Mac: Docker Desktop" below); on Windows it enables WSL2 and may need one
   restart.
2. **Installs DataLab** from the latest release into a user-level folder,
   using `uv`. `uv` brings its own Python, so there is no Python or Node setup
   to do, and no admin rights are needed for this step. Each version gets a
   folder of its own (see "Where the app lives" below), so an update never
   replaces the version in use. The installer also
   makes sure **Git** is present, which knowledge and pipeline syncing need.
   On Mac, Git comes with Apple's command-line tools. On Windows, the
   installer installs Git for Windows.
3. **Pulls the pinned images**, then asks for the keys (`datalab setup`).
   For the practice profile it also pulls Oracle Database Free (from
   Oracle's registry, pinned by digest in `datalab/practice_db`, tried again
   a few times when the registry is busy), for a first setup only, and
   never as a reason to stop (an in-app update runs the same `pull-images`),
   asks for no database password,
   and says the U-M GPT key is optional there and what it unlocks.
4. **Offers the GitHub sign-in** and clones the two lab repos, never as an
   administrator. The sign-in uses the "enter this code at
   github.com/login/device" flow (`datalab github sign-in`, the same code as
   Settings → GitHub), and is for the private knowledge-base and pipelines
   repos only; the app and images are public. Then `datalab repos sync`
   clones both. If GitHub says the account can't open a repo, it says whom
   to ask (`[repos] access_contact`) to be added to the `datalab-users` team,
   and the installer finishes everything else. For the practice profile this
   step is **Setting up the practice database** instead
   (`datalab --profile practice practice-db setup`): the container
   `datalab-practice-oracle`, published on 127.0.0.1 only, with its data in
   the labelled volume `datalab-practice-oracle-data`, loaded only when it
   has none, so a reinstall or update keeps it (synthetic/README.md, "In
   practice DataLab"). It never stops the install: practice DataLab sets
   the database up, or starts it, each time it opens. GitHub is skipped for
   the practice profile, when the lab's settings don't name the repos, or with
   `--no-github` (`-NoGitHub` on Windows); the person can sign in later in
   Settings. The installer refuses to run as root (`sudo`), and so does
   `datalab github sign-in`: the sign-in belongs in the person's own
   keychain.
5. **Adds a DataLab launcher**, named for its profile ("DataLab", or
   "DataLab (practice)") and with that profile's icon (see "Branding"):
   - Mac: an app in `/Applications` if the person can add to it without
     `sudo` (an administrator account can), otherwise in `~/Applications`.
     Only DataLab's own app (its bundle id) is ever replaced: another app of
     the same name in `/Applications` is left alone and DataLab goes in
     `~/Applications`; one in `~/Applications` stops the installer there,
     saying to move it (DataLab itself is installed by then). If DataLab's
     own copy in `/Applications` can't be replaced, it falls back to
     `~/Applications`; in `~/Applications` it says to quit DataLab and try
     again. An earlier installer's copy in the other folder is removed. A
     link to the app goes on the Desktop (`~/Desktop/DataLab`), replacing
     only a link to exactly `~/Applications/DataLab.app` or
     `/Applications/DataLab.app`. The launch script hands the program's path
     to AppleScript as an argument (`osascript - <path>`, `quoted form of`),
     so a home folder like `/Users/o'brien` works. The installer ends by printing where the
     app, the Desktop shortcut and the program files are, then, only when
     run from a terminal (not a pipe or script), offers "Show in Finder"
     (`open -R`) and "Open DataLab now?". `DATALAB_SYSTEM_APPLICATIONS`
     stands in for `/Applications` in tests.
   - Windows: a Start menu entry and a Desktop shortcut, the same for both.
     A Desktop shortcut of that name that doesn't run this `bin\datalab.cmd`
     is left alone, and the installer says so.
   Both run the launcher's command (`bin/datalab`, `bin\datalab.cmd`), never
   a version's own folder, so they keep working after an update. The icon is
   copied out of the package into the app bundle (Mac) or `<app>\icons`
   (Windows), so removing an old version doesn't take it away. After an
   update, and each time an installed DataLab starts, it copies its own
   package's icons over the launcher's where they differ
   (`launcher_icons.py`): on a Mac only a bundle of DataLab's own (its
   bundle id, launching this `bin/datalab`, not a link) and only if it's
   writable without an administrator, then `lsregister -f` so Finder and the
   Dock notice; on Windows only the `.ico` files already in `<app>\icons`.
   It never stops an update or a start.
   The keys are asked for during install (`datalab setup`). The first launch
   opens the Workspace, which says if no U-M GPT key is saved; Settings →
   Connections saves or replaces the key and password, and Settings → Safety
   runs the Safety check.

After install, DataLab is started by double-clicking. It opens in the browser
at a local address, and quitting from the app stops everything, including its
containers.

## Updating

- At startup, and about once an hour while it's open, DataLab checks GitHub
  for a newer release. If there is one, a pill says **Update available**; it opens Settings → **Updates**, which has
  the release notes and **Install update**.
- **Install update** asks the person to confirm, then installs the new
  version alongside the current one, pulls the new images, restarts, and
  asks the person to run the Safety check.
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

### Checking for a new version

`datalab/releases.py`. DataLab's host process asks the GitHub Releases API of
the app repo (`[updates] repository`, `SripadaLab-UM/ihs-datalab`) once at
start, in the background, if `[updates] check_on_start` is on (the default);
about once an hour while DataLab is open, if checking every hour is on; and
when the person presses **Check now** (at most once a minute). The browser
never makes this call, and containers can't: the check runs only in the host.

- **Every hour** (`releases.HourlyCheck`): a daemon thread started by
  `datalab serve` waits 60 minutes plus a random 0–5 (picked again each
  time, so DataLabs opened together don't ask GitHub at the same moment),
  then runs the same check as Check now, so it asks GitHub nothing more
  than that does, and never while GitHub asked DataLab to wait. It skips a
  round while an update is being installed (or DataLab is restarting into
  one) and while checking every hour is off; a round that fails is logged
  and the next comes an hour later. It logs (at info) only when the answer
  changes, and stops as DataLab quits. A new release it finds shows as
  **Update available** within five minutes (the pages read the last answer
  that often). `[updates] check_every_hour` (default on) is the starting
  value; the person can turn it off or on in Settings → Updates, which keeps
  that choice for this computer's DataLab in the data folder
  (`update-preferences.json`; practice keeps its own, in its own folder).

- **No sign-in.** The app repo is public, so releases are read without a
  token; the GitHub sign-in (for the lab repos) is never sent. While the repo
  was private GitHub answered 404, which shows as a quiet "can't check for
  updates" with whom to ask (`[repos] access_contact`); nothing else changes.
- **Offline, rate-limited, or GitHub down** are states, not errors: the
  Updates section says so in a sentence, and no pill shows. When GitHub asks
  DataLab to wait (a 403 with no requests left, or a 429), it doesn't ask
  again until the time GitHub gave.

**Which releases are offered.**

- Never a draft. The tag must be a PEP 440 version newer than the installed
  one, compared as versions (`v0.1.0-alpha.3` is 0.1.0a3, newer than 0.1.0a2
  and older than 0.1.0).
- **Channels** (`[updates] channel`): `stable` offers full releases only;
  `pre-release` offers pre-releases too; `auto` (the default) offers
  pre-releases while the installed DataLab is itself a pre-release, and only
  full releases after that. A release counts as a pre-release if GitHub marks
  it so or its version is one.
- Only a release with everything an update needs: the package for exactly
  that version (`datalab-<version>-py3-none-any.whl`), `requirements.txt`,
  `images.json`, `SHA256SUMS` and `SHA256SUMS.sig`. GitHub must give its own
  checksum (`digest`) of every one of them; a release where it doesn't is
  skipped, never checked less. The check reads `SHA256SUMS` and its
  signature, each checked against GitHub's checksum, verifies the signature
  (see "Release signing"), and `SHA256SUMS` must list the other three, each
  agreeing with GitHub's checksum. A release that doesn't pass is skipped (the
  log says why, or Updates when it's the newest) and the newest of the rest
  is offered.
- **Nothing at all while updates aren't set up**: until the lab's public key
  is pinned in the package (`release_keys.py`; the lab's key has been pinned
  since 2026-09-27), DataLab
  trusts no release, doesn't ask GitHub, and Updates says so. New versions
  are then installed with the installer.
- The release notes are shown as text, never as HTML.

### Release signing

A release's `SHA256SUMS` is signed with the lab's **release key** (Ed25519),
in the release workflow, as `SHA256SUMS.sig` (`datalab/signing.py`,
`scripts/sign-release.py`). The signing is a job of its own (`sign`), the only
one in the "release" environment: on a fresh runner, it gets the release's
files from the build job, checks them against `SHA256SUMS`, installs only
the tag's locked Python dependencies, signs, and hands back only the
signature. It never runs npm or the build, whose dependencies' install
scripts therefore never see the key (a CI test keeps it that way). Each DataLab trusts only the public keys pinned in
its own package (`datalab/release_keys.py`), and refuses a release with a
missing or bad signature. Since `SHA256SUMS` names every other file by its
checksum, including `requirements.txt` (every dependency by hash) and
`images.json` (every image by digest), the signature covers everything an
update installs. (Practice's Oracle Database Free image is pinned in the
package itself, `datalab/practice_db`, so the signature covers it too. It
isn't in `images.json`: every installed updater refuses an `images.json`
that lists anything but the agent, gateway and proxy images.) The package's name in `SHA256SUMS` carries its version,
which must be the tag's, so an old signed release can't be passed off as a
newer one.

**Changing keys.** The pinned keys are a list, and any of them will do. To
move to a new key, release a version that pins both the current and the new
key (signed with the current one, so every installed DataLab accepts it);
the release after that can be signed with the new key. The signing script
refuses a key the package being released doesn't pin, so a release is never
signed with a key its own version won't accept next time.

**What this protects against.** Someone who can change the app repo's
releases but doesn't hold the signing key: a GitHub token or account that can
upload or replace release assets, or create a release; a release file changed
on GitHub's storage or on the way; an index serving different dependency
files (their hashes are signed, and only PyPI is used). The key lives only in
the "release" environment, behind its required reviewers and limited to `v*`
tags, so a workflow run on any other branch or tag can't use it.

**What it doesn't.** Code merged into `main` and released the normal way;
the build's own dependencies (npm and Python packages, the build tools and
the GitHub Actions used): the package is built with them, and whatever they
put in it is signed with everything else;
anyone who can approve a run of the release environment, or steal the key;
GitHub withholding new releases (an update that never comes looks like none
being out); the first install, which trusts whatever installer and package
the person downloaded (the installer checks the package against
`requirements.txt`, but nothing checks the pair against the key); and uv and
the Python it downloads, which DataLab trusts as they are.

**Setting up release signing** (the maintainer, once; DataLab never makes
the key):

1. Make the key on your own computer:
   `uv run --project backend python scripts/sign-release.py --new-key`. It
   prints a private and a public key. Keep the private key only in the next
   step (a password manager copy is fine); never commit or paste it anywhere
   else.
2. In the app repo's Settings → Environments, create **release**, add
   **required reviewers** (the maintainers), restrict its **deployment
   branches and tags** to tags matching `v*`, and add the private key as the
   environment secret `RELEASE_SIGNING_KEY`.
3. In Settings → General → Releases, turn on **immutable releases**, so a
   published release's files can't be replaced.
4. In Settings → Rules, add a **tag ruleset** for `v*`: only maintainers may
   create these tags, and they can't be moved (updated) or deleted.
5. Replace the placeholder in `backend/src/datalab/release_keys.py` with the
   public key, and release. Versions from then on check updates; earlier ones
   (with the placeholder) never offer one, so people on them install the next
   version with the installer, once.

Until step 5, the release workflow stops at signing, so no unsigned release
is published. A release whose signing fails (or is never approved) still
leaves its agent image pushed to the container registry, since the image is
built first. That's harmless: DataLab runs images only by the digests in a
signed `images.json`, and a failed release publishes none.

### How an update is installed

`datalab/updater.py`, only after the person confirms, and only when nothing
is working (no agent turn, query, workflow run, pipeline test, or request
that changes something, such as an export or a sync, going), and the other
profile's DataLab isn't open:

1. **Download and check.** The package, `requirements.txt` and
   `images.json` go to `<app>/downloads/<version>/`, each refused unless it
   matches the signed `SHA256SUMS`. `requirements.txt` must pin every
   dependency as `name==version` with hashes, name the package once, by its
   checksum, and hold nothing else (no index, no URL, no `-e`). `images.json`
   must pin the agent, gateway and proxy images by digest, and name exactly
   the images the new package runs (its `release.json` and `containers.py`).
   Nothing else has changed yet.
2. **Close the gate, stop conversations, then back up.** From here on
   DataLab refuses (409) every request that could start or change something
   (`update_gate.py`), and every page shows a banner; it checks once more
   that nothing began during the download. Then `updates.begin`: marker
   "started", then "backed-up" (see "The update marker" below).
3. **Install beside the old version**: `uv venv` and
   `uv pip install --no-config --require-hashes --only-binary :all:
   --default-index https://pypi.org/simple -r requirements.txt` into
   `<app>/versions/<version>/`, with no `UV_*`, `PIP_*` or `PYTHONPATH` from
   the environment and no uv config files; check the new `datalab --version`,
   then the new version pulls its own pinned images. Marker "installed".
   `.complete` records the package's checksum, so a folder is reused only
   for exactly the same package.
4. **Switch the launcher**: `<app>/current` now names the new version and
   `<app>/previous` the old one. Marker "switched". Versions older than
   those two are then removed (program files only, never data).
5. **Restart**, once what began before the gate closed has finished (it
   waits up to a minute; otherwise it asks the person to quit and reopen,
   with the gate still closed). A small helper, run by the old version's
   own Python in isolated mode (`python -I`, from the install folder), waits
   for this DataLab to quit, opens the new one as the launcher does (a
   Terminal window on Mac, PowerShell on Windows), and waits for it to
   finish the update. If the new version doesn't start, it puts `current`
   back and opens the old version, whose startup recovery sorts out the
   marker. It looks once more before going back, and doesn't if the update
   finished after all or something holds the data folder. A new version that
   is still starting (a long migration) is never interrupted.

If step 3 or 4 fails, the launcher is put back, what the step installed is
removed, the marker cleared ("abandoned") and the gate opened; the backup
stays. The running
version's folder is never touched, and a version folder only counts once it
has `.complete` (written last), so a cut-off install is redone next time.

**Going back.** The previous version stays installed.
`datalab versions` lists the installed versions, and
`datalab versions --use <version>` points the launcher at one (not while an
update's marker is there: opening DataLab sorts that out first). If the newer
version had changed the database, the older one says so at start; then
`datalab rollback` (run with the older version) restores the update's backup.

### Where the app lives

Beside the data folders, in `~/Library/Application Support/DataLab/app` on
Mac and `%LOCALAPPDATA%\DataLab\app` on Windows (`DATALAB_INSTALL_DIR` for
tests):

```
versions/<version>/   one Python environment per version; .complete when whole
current               the version the launcher opens
previous              the one it opened before the last switch
bin/datalab           the launcher's command: runs `current` (datalab.cmd on Windows)
downloads/<version>/  a release's files while it's being installed
```

The launchers (DataLab.app and its Desktop shortcut, the Start menu entry and
Desktop shortcut on Windows) run `bin/datalab serve`, so switching `current`
is all an update changes in them (`tests/test_installer_macos.py` updates and
prunes a version as the updater does, then opens the app through its Desktop
shortcut). If the version
`current` names can't run, the Mac `bin/datalab` falls back to `previous`
and says so; by hand, `versions/<old>/bin/datalab versions --use <old>`
(`Scripts\datalab.exe` on Windows) points it back.

**The real and practice DataLabs share all of this**: the versions, `current`
and `previous`. An update switches both, so it refuses while the other
profile's DataLab is open. Each has its own launcher, "DataLab" and
"DataLab (practice)" with icons of their own, so installing one never
replaces the other's. An installer from
before this layout used `uv tool install`; that copy can't update itself
(Updates says so), and the new installer replaces it.

### How updating keeps the database safe

This part is built (milestone 7), and Settings shows it (**Updates**: the
version, the check for a new one, an update in progress, what the startup
recovery did, and the backups). The updater above calls it.

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
  itself be undone. These backups are kept indefinitely; Settings →
  Storage shows them and deletes one only after the person confirms that
  it may hold the only copy of what the rollback dropped.
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
playground/<pg_id>/    the SQL Playground's results, outside every conversation
repos/ihs-knowledge/   the lab knowledge base (git)
repos/ihs-pipelines/   the lab pipelines and workflows (git)
support/<report-id>/   saved support reports (Send feedback)
catalog/               the practice DataLab's catalog, built from its database
practice-exports/      the practice DataLab's only export folder
logs/                  metadata only, never data
```

**Export folders** are the only other places DataLab writes. They are
named folders the person adds in Settings → Export folders, each chosen in
the computer's own folder picker (`export_folders.py`):

- **Dropbox** (and OneDrive, Box, Google Drive, iCloud Drive): found by
  name in the home folder and, on a Mac, `~/Library/CloudStorage`, so the
  picker can open there; the person picks a subfolder. If none is found,
  the section says so.
- The practice DataLab exports only to its own `practice-exports` folder in
  its data folder.

**Nothing is deleted automatically.** Settings has a **Storage** view that
shows disk use: the database, each conversation (workspace, checkpoints,
query results), the SQL Playground's results
(`<data_dir>/playground/<pg_id>/results/`, one CSV per query), each workflow
run's folder, the backups, exports and logs. This matches the promise
"nothing is deleted unless you delete it". The person can remove, one at a
time and each confirmed (`datalab/storage.py`):

- a Playground result, unless its query is running (the query stays in the
  history);
- a finished run's files, unless a Replay of it is going. Its record stays,
  in the database and as `record.json`; the run can't be replayed after;
- a backup, except one an update in progress relies on. A rollback's backup
  needs an extra confirmation.

Never the database, the audit log, exports, or anything else; items are
named by id, never by path, and links are never followed. Conversations are
deleted from the Workspace.

## Uninstalling

The uninstaller removes the app, the launchers and their Desktop shortcuts
(on Mac, apps in either Applications folder only if their bundle id is
DataLab's, and Desktop links only if they point at exactly one of those apps;
on Windows, Desktop shortcuts only if they run this DataLab's
`bin\datalab.cmd`), the images, and the keychain
entries. It asks separately, showing sizes, whether to delete the data folder.
It never touches export destinations. On Windows it also removes what the
installer left: the after-restart logon task or Startup shortcut, its
progress files, and the elevated part's result folder in `%ProgramData%`. It
leaves Docker Desktop, WSL and `docker-users` membership as they are (see
Windows specifics). On Mac it leaves Docker Desktop too, including one the
installer added; Docker's own Troubleshoot → Uninstall removes it (with its
containers, images and volumes).

## Mac: Docker Desktop

Step 1 of `installer/macos/install.sh` (shipped as `install-macos.sh`):

- **Finding it.** Docker Desktop is looked for in `/Applications` and
  `~/Applications`, and its `docker` command on PATH, then inside the app
  (`Docker.app/Contents/Resources/bin/docker`) and in `~/.docker/bin`, so a
  missing `/usr/local/bin/docker` link doesn't matter. A `docker` on PATH
  that links to a Docker.app somewhere else (renamed, or in a subfolder) is
  followed to that app, and failing that, Spotlight (`mdfind` for bundle id
  `com.docker.docker`) is asked before offering to install one, so a second
  copy isn't installed beside it. Either way only an app in an Applications
  folder (`/Applications`, `~/Applications` or a folder inside them) counts:
  not Docker's own staging copy from a half-done install or uninstall
  (`~/Library/Application Support/com.docker.install/in_progress/`), the
  Trash, a disk image or a cache. When Docker doesn't come up, the message
  also mentions quitting leftover Docker programs or restarting the Mac. The
  command found is used by its full path, and its folder goes last on PATH
  (so it hides nothing already there) for the rest of the install (it also holds Docker's credential helpers, which `docker
  pull` uses). DataLab does the same (adding it last) each time it starts
  (`datalab/docker_path.py`, called from `datalab.cli.main`), so the app
  launched from Finder or the Desktop works without the link too.
- **Running already:** nothing is done. **Installed but stopped:** it opens
  the app and waits until `docker info` answers (up to 5 minutes, saying
  every 30 seconds that it's still waiting; each `docker info` is given 20
  seconds, and the wait is timed by the clock). A `docker` call that hangs
  is stopped by a parent process (perl, or a background watcher without
  perl): SIGTERM, then SIGKILL 2 seconds later, since docker (a Go program)
  ignores SIGALRM. If an existing Docker Desktop's programs are running but
  its engine hasn't answered for 90 seconds, it runs `docker desktop
  restart` once (not on a first run, whose window may be waiting for the
  person, nor on a Docker without that command). If the engine still
  doesn't answer, it says to Restart from the whale menu, then Troubleshoot,
  and that "Clean / Purge data" and "Reset to factory defaults" delete
  Docker's containers and images. A Docker.app that isn't
  complete (no program named by its `CFBundleExecutable`) is reported, with
  "drag it to the Trash and run this again". An existing Docker Desktop is never reinstalled, upgraded, reset
  or reconfigured; the installer never prunes or removes containers, images
  or volumes, and never touches Docker's settings or data
  (`~/Library/Group Containers/group.com.docker`).
- **Missing:** it says what it will do and asks
  `Download and install Docker Desktop? [Y/n]` (pressing Return is yes, as
  the person has chosen to install DataLab, which needs it, and macOS asks for
  their password before anything is installed; n or no stops; with no
  terminal to answer it's no; `--install-docker` answers yes beforehand). Before asking it
  checks the Mac: Apple silicon (`sysctl -n hw.optional.arm64` is 1, which is
  also true under Rosetta, where `uname -m` says x86_64) or Intel; macOS 14
  or newer (Docker supports the current and two previous major releases; 14
  is Docker Desktop 4.93's own minimum); and about 6 GB free. Then:
  1. Downloads `https://desktop.docker.com/mac/main/<arm64|amd64>/Docker.dmg`
     (https and TLS 1.2 or later only; given up on if it stalls under
     10 kB/s for 2 minutes; resumable: a partial download in
     `~/Library/Caches/DataLab/docker-desktop/`, created only after the
     person says yes, is continued next time). Docker signs the disk image
     itself, so its signature (Docker Inc, team `9BNSXJN65R`) is checked
     before it's opened.
  2. Attaches it read-only at a private mount point (not `/Volumes`), and
     checks the app inside before anything from it runs: `codesign --verify
     --deep --strict` against the requirement "Apple-anchored, leaf
     certificate OU `9BNSXJN65R`, identifier `com.docker.docker`",
     `codesign -dv` showing that team and identifier, and `spctl -a -vv -t
     exec` saying `source=Notarized Developer ID` and origin
     `Docker Inc (9BNSXJN65R)`. A symlinked Docker.app is refused. If
     Gatekeeper is off (spctl gives no origin), it says so. It also checks the
     app's own `LSMinimumSystemVersion` against this Mac. Any failure: the
     image is detached, the download deleted, and nothing installed.
  3. On an administrator account (`admin` in `id -Gn`), runs Docker's own
     supported command-line installer from the checked image:
     `sudo <image>/Docker.app/Contents/MacOS/install --user <user>`, never
     with `--accept-license`. It says first that macOS will ask for the
     password; sudo reads it from the terminal (nothing is passed to it).
     Docker's installer puts the app in `/Applications` and sets up its
     privileged helper and `/usr/local/bin` links; the installed app is then
     checked again. If sudo is cancelled or the installer fails, it stops
     with what to do (the download is kept and checked again next time).
     Otherwise (not an administrator, or no install command in the image):
     copies the app with `ditto` (which keeps its signature) to
     `/Applications` if the account can add to it without `sudo`, otherwise
     `~/Applications` (Docker works from there; the first-run window then
     needs "Use advanced settings" with the command line tools set to
     "User" if there's no administrator password). The copy goes under a
     temporary name (`.Docker.app.datalab-partial`, removed however the
     installer ends, Ctrl-C included), is checked again, then renamed, so a
     half-done copy is never taken for Docker Desktop. If a Docker.app
     appears meanwhile, the copy is removed and that one left alone. If
     macOS refuses the copy with "Operation not permitted" (Privacy &
     Security → App Management, macOS 13 and later), it says to allow
     Terminal there or use `~/Applications`. Then the image is detached and
     the download deleted.
  4. Opens Docker Desktop and prints what its first-run window asks: the
     Docker Subscription Service Agreement, which the person reads and
     accepts themselves (the installer never accepts it for them), "Use
     recommended settings" (and the Mac password, for Docker's helper), and
     that signing in is optional (Skip). It waits up to 15 minutes for `docker
     info`, then the DataLab install carries on by itself.
- **When it stops**, it says why and what to do, and that running the
  installer again carries on from there: download failed (resumed next
  time), download damaged or not signed by Docker (deleted), the person
  said no, the copy was blocked (MDM or permissions: install from the
  organisation's Self Service or ask IT), Docker quit before it was ready
  (usually the agreement declined), Docker didn't start in time (answer its
  window, or Restart from its whale menu), not enough disk space, an
  unsupported processor, or macOS too old. Ctrl-C says the same. Nothing is
  done twice on a re-run: an installed Docker Desktop is found and started.
- The prompt says Docker Desktop's license terms apply to its use and that
  an organisation may have its own guidance; it doesn't say what a
  particular organisation's license status is.
- Readiness is `docker info` only: DataLab doesn't use Docker Compose.
- Tests (`backend/tests/test_installer_macos.py`) run the real script
  against stand-ins for `docker`, `open`, `curl`, `hdiutil`, `codesign`,
  `spctl`, `sw_vers`, `sysctl`, `uname`, `df`, `ps`, `mdfind`, `ditto`,
  `id`, `sudo`, Docker's `install`,
  `date` and `sleep` (a fake clock).
  `DATALAB_DOCKER_WAIT_SECONDS` and `DATALAB_DOCKER_POLL_SECONDS` change the
  wait; `DATALAB_INSTALL_STOP_AFTER_DOCKER=1` stops once Docker is ready,
  before anything is installed.

## Windows specifics

- Docker Desktop needs WSL2. That means one administrator step and usually a
  restart; the installer resumes where it left off. On Michigan Medicine
  managed machines this may need temporary elevation, the normal JIT process,
  so the installer asks people to request it before they continue.
  - Built (`install.ps1`): the installer asks Windows for permission once, up
    front, and only if something is missing. That one step turns on the WSL
    features, installs WSL and Docker Desktop (pinned versions, each checked
    by SHA-256 and by its publisher's signature), and adds the person to
    `docker-users` by SID (a name lookup needs the domain controller, which
    isn't reachable off the VPN). Then it offers the restart and opens again
    after sign-in, from a logon task for that person; nothing after this
    needs an administrator.
  - The elevated part runs nothing the person's account could change, as far
    as the installer can arrange it:
    - It's started with `-EncodedCommand`, with its values (paths, SID)
      inside as Base64 text, so no quoting is involved. The command reads a
      copy of the installer's text once, checks its SHA-256 against the text
      the person's window is running, and runs it from memory.
    - Before that, it sets `PSModulePath` to Windows' own module folders
      (`$PSHOME\Modules` and `Program Files\WindowsPowerShell\Modules`),
      turns module autoloading off, and loads the modules it uses (including
      `Dism` and `Microsoft.PowerShell.LocalAccounts`) by full path from
      `$PSHOME`. The person's own module folders and `PSModulePath` aren't
      used. `powershell.exe` and `msiexec.exe` are started by full path from
      the Windows system folder, never looked up by name.
    - ProgramData's location comes from Windows (`CommonApplicationData`),
      not `%ProgramData%`, which a person can set for their own account. The
      elevated part accepts only a folder named exactly
      `DataLab-setup-<32 hex digits>` there. It checks that ProgramData
      isn't a link, is owned by SYSTEM, TrustedInstaller or Administrators,
      and doesn't let any other account delete, replace or re-permission
      what's in it; otherwise it stops and says to ask IT.
    - That folder is new, and only SYSTEM and Administrators can use it
      (created with those permissions, inheritance off, checked before use).
      Both downloads go there and are checked for their SHA-256 and their
      Authenticode signature, whose certificate must name the exact
      organisation (`Microsoft Corporation` for WSL, `Docker Inc` for Docker
      Desktop). They run from there, with `TEMP` pointed inside the folder
      too. At the end it deletes the downloads and lets the person read the
      result and log and remove the folder.
    - Removing that folder afterwards: anyone can create folders in
      ProgramData, so the installer never removes anything there by name or
      pattern. Before starting the elevated part, it records the exact
      folder in `%LOCALAPPDATA%\DataLab\installer-admin-folder.txt`, which
      other accounts can't write. It removes only recorded folders, after
      reading the result or on a later run, and only if the folder has
      exactly the owner and permissions the elevated part sets: SYSTEM or
      Administrators as owner, inheritance off, no Deny rules, and no rules
      but SYSTEM and Administrators (full control), OWNER RIGHTS (read
      permissions) and the person (list, read attributes, read permissions,
      delete, on the folder only). It checks again that the folder isn't a
      link right before removing it, and never follows a link inside it.
      A folder that doesn't pass is left alone, with a note to ask IT.
      `uninstall.ps1` does the same.
    - The installer stops at once if PowerShell runs in Constrained Language
      Mode (AppLocker or WDAC), or if it's the 32-bit PowerShell on 64-bit
      Windows.
    - **For IT:** endpoint protection (Defender attack surface reduction
      rules, CrowdStrike and the like) may block or flag an elevated
      `powershell.exe -EncodedCommand`, which is how the elevated part
      starts. If it's blocked, the elevated window doesn't open or closes at
      once, and the installer says the administrator part closed before it
      finished. Allow it for this install, or run the installer for the
      person.
  - A Docker Desktop that was already installed, probably without
    `--always-run-service`, gets its service (`com.docker.service`) set to
    start automatically, in the same elevated step. A service IT has
    disabled is left alone.
  - If Windows restarts and the person still can't use Docker (for example,
    a group policy removes them from `docker-users` at each sign-in), the
    installer doesn't ask for another restart: it stops, names the likely
    cause, says to ask IT, and offers to run the elevated part again (never
    with `-Yes`, so it can't restart at every sign-in). The same goes for
    anything else the elevated part fixed that is missing again after a
    restart (for example, a policy that sets Docker's service back to
    manual): the installer stops and names it, rather than running the
    elevated part and restarting again.
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
    - Membership of `docker-users` is what lets an account control Docker
      Desktop's service, which runs as SYSTEM. So IT should treat
      `docker-users` as a privileged group: add only the people who use
      DataLab on that computer, and review who is in it.
  - Before starting Docker Desktop, it moves aside socket files an earlier
    Docker left behind, and stops a leftover Docker VM: either stops Docker
    Desktop from starting.
  - uv cuts a path at its first space, and downloads on these machines
    usually sit under `OneDrive - Michigan Medicine`, so the installer hands
    uv copies of the package and `requirements.txt` under plain names, from
    their own folder.
  - The person's part installs like the Mac installer (see "Where the app
    lives"): each version in `%LOCALAPPDATA%\DataLab\app\versions\<version>`,
    with `uv pip install --no-config --require-hashes --only-binary :all:
    --default-index https://pypi.org/simple -r requirements.txt`, no `UV_*`,
    `PIP_*` or `PYTHONPATH` from the environment, and the package checked
    against its line in `requirements.txt` first (the log says "Checked:
    SHA-256 of ..."). The Start menu entry and the Desktop shortcut
    ("DataLab", or "DataLab (practice)" with `-Practice`, each with its own
    `.ico`) run `bin\datalab.cmd`, which opens the version `current` names. A copy an older installer made with
    `uv tool install` is removed once the new one is in place.
  - Without `-Package`, the installer uses the one
    `datalab-<version>-py3-none-any.whl` in its own folder, so the command in
    the README works as written from the folder the release's files were
    downloaded to.
  - Before installing (step 4) it checks that DataLab isn't running: a
    DataLab process (from the installed versions or an older installer's
    copy) or something listening on 8765 or 8766. If one is, it asks the
    person to close it and waits; with `-Yes` it stops and says so. It never
    stops DataLab itself. (Re-running an older installer while DataLab ran
    failed with "Access is denied" removing uv's copy.)
    - **For IT:** under `-Yes`, *anything* listening on 127.0.0.1 or any
      address at port 8765 or 8766 stops the install, with a message saying
      which, even if it isn't DataLab. Free the port or run it interactively.
  - uv comes from its own release, pinned: step 3 downloads
    `uv-x86_64-pc-windows-msvc.zip` for the version in `install.ps1`
    (`$UvVersion`) from `github.com/astral-sh/uv/releases`, checks it
    against the pinned SHA-256 (`$UvZipSha256`), unpacks it as the person
    into `%LOCALAPPDATA%\DataLab\uv`, and checks that `uv.exe` is
    Authenticode-signed by `$UvPublisher` ("OpenAI OpCo, LLC" for uv
    0.12.19). Steps 4 onward use that `uv.exe` by full path, never one on
    `PATH` (DataLab's updater uses it too). No script is piped from the
    web. **To move to a newer uv**, change `$UvVersion` and `$UvZipUrl`
    together, set `$UvZipSha256` from that release's
    `uv-x86_64-pc-windows-msvc.zip.sha256` file (it should match GitHub's
    digest for the zip), and check the signer's organisation hasn't changed
    (CI's windows-installer job downloads the pinned zip and checks all
    three).
  - `bin\datalab.cmd` (and the Mac `bin/datalab`) set `PYTHONUTF8=1`, so
    DataLab's Python reads and writes UTF-8 whatever the code page: a
    Windows smoke test hit cp1252 errors without it.
  - After the keys, it offers the GitHub sign-in and syncs the lab repos
    (step 7), as the person, never elevated; skipped with `-Practice`,
    `-NoGitHub` or `-Yes`. `-Practice` and `-NoGitHub` are kept across the
    restart.
  - Each download the administrator part checks is logged as
    "Checked: SHA-256 and signature (<organisation>)", with the organisation
    read from the signing certificate, for audits.
  - The administrator window turns QuickEdit off for itself (through the
    console API, defined in memory, not `Add-Type`): a click in it otherwise
    selects text and pauses it until Esc. The person's console settings and
    the registry aren't changed.
  - The `docker-users` checks look first (the group, then its members,
    quietly), so the expected cases (no group yet, already a member) don't
    show up in logs as errors.
  - `uninstall.ps1` removes every installed version, both Start menu
    entries and both Desktop shortcuts: only `versions`, `bin`, `icons`,
    `current` and `previous` in the app
    folder (the folder itself only if nothing else is left in it), the
    installer's staging folder and DataLab's own uv. It ends by saying what
    it leaves installed (Docker Desktop, WSL and its Windows features, uv's
    downloads, `docker-users` membership) and how to remove each.
- Credentials go in Windows Credential Manager, and paths use
  `%LOCALAPPDATA%`.
- **Updating on Windows** follows the same steps as on Mac, with the Windows
  paths (`Scripts\datalab.exe`, `bin\datalab.cmd`), the helper started
  detached, the new version opened in a new PowerShell window, and a process
  check through the Windows API. Tried once on the re-test machine
  (0.2.0b3 to 0.2.0b4, practice profile): backup, install beside, switch and
  restart took about 20 seconds, and `datalab versions --use` went back and
  forward again.
- **Docker's virtual machine and "Log on as a service".** WSL 2's virtual
  machine signs in as `NT VIRTUAL MACHINE\Virtual Machines` (S-1-5-83-0),
  which needs the "Log on as a service" right. Hyper-V adds it when Windows
  starts; Michigan Medicine's security policy ("CoreOne-Security") sets that
  right to its own list and takes it away again whenever Windows re-applies
  it on the network. Then no WSL virtual machine starts (`wsl.exe`:
  `HCS/0x80070569`, "the user has not been granted the requested logon
  type") and Docker Desktop waits for its engine for ever, saying nothing.
  Restarting Windows fixes it until the next time. So does adding that one
  right back, which needs an administrator (asking IT would take too long):
  - The installer's administrator part adds it; Step 2 checks before starting
    Docker and, if it's missing, offers the fix behind one administrator
    prompt.
  - DataLab itself checks too, whenever Docker isn't answering
    (`datalab/windows_vm.py`, `api/docker.py`); this is the part that reaches
    people through updates. While Docker can't run, every page shows a banner
    at the top: "Open Docker Desktop" when it's closed (DataLab also opens it
    by itself when it starts, once), a note while it's starting, "run the
    installer again" when it isn't installed, and, when the right is missing,
    "How to fix it…", which opens a dialog by itself once a session (until
    Later). The dialog says to turn on the temporary administrator
    access first, linking to the page for it when the lab's settings file
    names one (`[windows] admin_access_url`; otherwise it says where in
    words: on a Michigan Medicine computer, the profile page), then
    to press Fix it, which shows one Windows permission box and restarts
    Docker Desktop. It says what happened: fixed, declined (most often: the
    administrator access wasn't on yet, so turn it on and press Fix it
    again), or still blocked (restart Windows). Fix it is refused unless the
    last check found the VM refused, so it can never raise a prompt while
    Docker works.
  - After the right is back, Docker Desktop is restarted afresh
    (`windows_vm.restart_docker`): once its engine has given up waiting for a
    VM Windows refused, it never tries again, and `docker desktop restart`
    left that stuck backend running (0.3.0b3 on the re-test machine; only a
    Windows restart cleared it). The steps, each with a time limit and none
    repeated: end Docker Desktop's own processes (`Docker Desktop.exe`,
    `com.docker.backend.exe`, `com.docker.build.exe`, `docker-sandbox.exe`,
    by process id, only those running as this account, as tasklist's
    USERNAME filter gives them; never its SYSTEM service, com.docker.service),
    `wsl --terminate docker-desktop` (never `wsl --shutdown`), open Docker
    Desktop, then wait up to 4 minutes for `docker info`. The dialog names
    the step that didn't work and says to restart Windows. This stops
    everything in Docker, so the fix is refused while a conversation's turn,
    a query, a workflow run or a pipeline test is going, and the dialog says
    so before the button is pressed. On the re-test machine, with Docker
    working, the restart took 14 seconds. Docker Desktop 4.77 and 4.93 have
    only the `docker-desktop` distribution; older ones' `docker-desktop-data`
    is left running, which is harmless. A Docker Desktop started elevated
    lists with no user name, so it isn't closed, and the restart fails at
    "ready". While the fix runs, the page sees its phase (`prompt`, then
    `restarting`): during the restart the state is "starting", and Open
    Docker Desktop does nothing, so it can't race the restart.
  - The installer's Step 2 does the same after its own fix (Repair-VmLogon):
    if Docker Desktop is already open, it closes its programs (the same four,
    this account's only, by process id with the image and user filters) and
    runs `wsl --terminate docker-desktop`; Step 2 then opens Docker Desktop
    and waits, and says "restart Windows" if it doesn't get ready.
  - How often it looks, since a check can start WSL's VM: the page asks
    every 30 seconds while something is wrong and every 5 minutes
    otherwise (the policy can take the right away while DataLab is open);
    `GET /api/docker` answers from a check at most 15 seconds old, whoever
    asks (another page on this computer can send a GET); Check again is a
    `POST /api/docker/check`, at most every 5 seconds. WSL's VM is only
    tried while Docker Desktop is open but not answering, never while it's
    closed, and once it has started it isn't tried again for 5 minutes
    while Docker Desktop is still starting. One check runs at a time; others
    get the last answer meanwhile. The practice and real DataLabs check on
    their own, so with both open each can offer the fix.
  - The fix is a button, never a question in DataLab's window: in the first
    real use, the question came before the person could turn on their
    administrator access, and there was no way back to it without quitting.
    DataLab's window only says what's wrong and points to the page. Running
    DataLab itself as an administrator isn't needed, and isn't recommended:
    only the fix needs it, for a moment.
  - Checking whether the right is there needs an administrator, so both
    start WSL's own system distribution (`wsl.exe --system -e true`), never
    Docker's, and look for that code.
  - The fix is fixed text (`GRANT_SCRIPT`, the same as the installer's
    `$VmLogonGrant`; a test checks they match) run from `-EncodedCommand`. It
    calls `LsaAddAccountRights` for that one account and that one right,
    through methods defined in memory rather than `Add-Type`, which compiles
    into the person's own TEMP folder.
- Windows must be tested on a real managed machine before it's promised to
  colleagues. The installer's first version was re-tested on one: a Michigan
  Medicine Windows 11 Enterprise (26100) laptop with CrowdStrike, Defender
  and CyberArk EPM, PowerShell in FullLanguage mode, no AppLocker, and a
  user profile owned by SYSTEM.
- **Testing notes.** Start the installer from a normal PowerShell window, or
  through Task Scheduler. Not from an app packaged as MSIX (the Claude
  desktop app is one): Windows redirects the AppData writes of anything such
  an app starts into the app's own package folder, so the installer's state,
  DataLab's versions and the Start menu entry would land where a normal run
  never finds them. On the re-test machine the installer was started as a
  one-off scheduled task for the person's account.

## Branding

DataLab is an internal tool of the University of Michigan's Intern Health
Study, and its logo is the team's own: U-M's **Block M** (traced from the
prototype's artwork, `um-gpt-local-proxy/deploy/cognito/logo.png`, in Maize
`#FFCB05` on Blue `#00274C`) with **"IHS"** centred under it and a big
four-point **spark**, the usual AI cue, over the M's top-right corner. The
spark is edged in the tile's colour, so it reads where it crosses the M.
`DESIGN = "1b"` in `branding/build.py` picks this; the earlier options
(`"1"`, `"2"`, `"3"`) are a one-line change away, and
`build.py --options <folder>` draws every design with preview sheets.

- **App icon:** the whole design from 64 pixels up; at 32 pixels the M and a
  smaller spark; at 16 pixels the M alone.
- **Favicon:** the SVG is the M alone (a tab draws it at 16 pixels); the
  32-pixel PNG has the spark.
- **Practice:** the same icons inverted, Blue on Maize: a yellow square
  instead of a blue one, the clearest difference there is at 16 pixels.
- **Header:** the 32-pixel tile where the mark always was (22 pixels), then
  from 1280 pixels wide the IHS mark (Blue on light, Maize on dark) and the
  "DataLab" wordmark. Nothing else in the app changes colour.

`branding/build.py` holds the geometry and writes everything made from it:

| File | Used by |
| --- | --- |
| `branding/block-m.svg`, `branding/ihs-mark.svg` | the two marks on their own |
| `branding/datalab-mark[-practice].svg`, `-1024.png` | the app icon, for docs and slides |
| `frontend/src/app/brandArt.ts` | the header's copy of both marks (`brand.tsx`) |
| `frontend/public/favicon[-practice].svg` | the browser tab |
| `frontend/public/favicon[-practice]-32.png`, `apple-touch-icon[-practice].png` | browsers without SVG favicons |
| `backend/src/datalab/branding/DataLab[-practice].icns` | the Mac app (in the package, copied into the app) |
| `backend/src/datalab/branding/DataLab[-practice].ico` | the Windows shortcuts (copied to `<app>\icons`) |

Regenerate with `uv run --no-project --with pillow python branding/build.py`
(on a Mac it uses `iconutil` for the `.icns`), and commit what it writes. On
practice the app also swaps the tab's icon and title for practice's.
Browsers keep a favicon by its URL, so the build names each tab icon with its
content's hash (`/favicon.svg?v=<sha8>`, `frontend/brandIcons.ts`), in
`index.html` and in the practice swap, and DataLab serves them with
`Cache-Control: no-cache`: a new icon shows after an update.

## Connectivity

- **Oracle needs the Michigan Medicine VPN.** DataLab's host process does the
  database connections, which avoids the prototype's trouble with Docker
  reaching the VPN. When Oracle is unreachable (its name won't resolve, or the
  connection fails), the app says "Can't reach the database. Connect to the U-M
  VPN (or check your network), then test again." instead of failing obscurely.
  Settings → Connections tests the database and U-M GPT independently: one
  failing never hides the other's result.
- **U-M GPT** is reached through the gateway container.

## For the maintainer: releasing

- Tagging a release makes GitHub Actions:
  - run all checks, including the upgrade-and-rollback test;
  - build the app with the frontend included;
  - build and push the agent image (the gateway and research proxy are
    upstream images, pinned by digest in the code). If `images/agent` hasn't
    changed since an image already in the registry (tagged `tree-<its git
    tree id>`), that image is reused as it is, so a release that changes only
    the app installs without a new image to pull;
  - publish a release that lists the exact image digests (also in
    `images.json`), with the installers, `requirements.txt` (every
    dependency by hash, then the package by its checksum), a `SHA256SUMS`
    file and its signature, `SHA256SUMS.sig`, made in the protected
    "release" environment (see "Release signing"). The update check offers
    only releases with all of these (see "Which releases are offered"); a
    pre-release is tagged `v0.1.0-alpha.3` and marked so.
- Not automated yet: signing the Windows scripts and anything macOS runs
  directly (they need the lab's signing identities), and the Windows test on a
  real managed machine. `release.yml` marks each as a TODO.
- Everything is pinned: Python dependencies (`uv.lock`), npm
  (`package-lock.json`), base images, and the Codex CLI version.
- **Diagnostics instead of bug-report uploads.** "Copy diagnostics" in
  Settings produces a metadata-only report (`datalab/diagnostics.py`):
  versions, OS and Docker, the profile, data-folder paths with the home
  folder as `~`, whether keys are saved (never the keys, the database server
  or account), Safety check results by check id, the database layout and
  update state, failure counts, and recent problems as DataLab's own message
  templates without their values. The user pastes it into an email.
- **Support reports, without uploads to Dropbox or the public repo.**
  Send feedback packages a bug report or suggestion as a ZIP (a summary,
  allowlisted diagnostics, a manifest, and files the person chose), saved
  under the data folder. The person saves it to an export folder and emails
  it, or, on real DataLab, sends it in one click to the lab's private
  support repository (`[repos] support`) with their GitHub sign-in. It's
  called delivered only when GitHub confirms the commit. See
  [SUPPORT.md](SUPPORT.md), which also says how to set the repository up.
  The prototype's feedback flow could zip query results and upload them
  through Dropbox's API; nothing like that exists in v1.

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
