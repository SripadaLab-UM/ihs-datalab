# Installing, updating, and running DataLab

Status: **draft** for v1. This is a proposal under discussion. Implemented so
far: the installers, with versions side by side and the GitHub steps; the
update check, the **Update available** pill and the updater (Mac tried;
Windows covered by unit tests only, see "Windows specifics"); and the data
side of updating (database backups, `datalab rollback`, and recovering from
an interrupted update; see "How updating keeps the database safe" below).

Goal: a colleague with no technical background can install DataLab in about
15 minutes, and after that never needs a terminal.

## What ends up on a user's computer

| Piece | What it is | Where it comes from |
|---|---|---|
| Docker Desktop | Runs the sealed agent containers | Docker, installed or checked by the installer |
| DataLab app | The host program: web UI, data service, and orchestration. One folder per version, side by side | A GitHub release, with exact pinned versions |
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

1. **Checks Docker Desktop.** If it's missing, the installer downloads and
   installs it. This asks for an administrator password on Mac, and on
   Windows it enables WSL2 and may need one restart.
2. **Installs DataLab** from the latest release into a user-level folder,
   using `uv`. `uv` brings its own Python, so there is no Python or Node setup
   to do, and no admin rights are needed for this step. Each version gets a
   folder of its own (see "Where the app lives" below), so an update never
   replaces the version in use. The installer also
   makes sure **Git** is present, which knowledge and pipeline syncing need.
   On Mac, Git comes with Apple's command-line tools. On Windows, the
   installer installs Git for Windows.
3. **Pulls the pinned images**, then asks for the keys (`datalab setup`).
4. **Offers the GitHub sign-in** and clones the two lab repos, never as an
   administrator. The sign-in uses the "enter this code at
   github.com/login/device" flow (`datalab github sign-in`, the same code as
   Settings → GitHub), and is for the private knowledge-base and pipelines
   repos only; the app and images are public. Then `datalab repos sync`
   clones both. If GitHub says the account can't open a repo, it says whom
   to ask (`[repos] access_contact`) to be added to the `datalab-users` team,
   and the installer finishes everything else. It's skipped for the practice
   profile, when the lab's settings don't name the repos, or with
   `--no-github` (`-NoGitHub` on Windows); the person can sign in later in
   Settings. The installer refuses to run as root (`sudo`), and so does
   `datalab github sign-in`: the sign-in belongs in the person's own
   keychain.
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
  pill says **Update available**; it opens Settings → **Updates**, which has
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
start, in the background, if `[updates] check_on_start` is on (the default),
and again when the person presses **Check now** (at most once a minute). The
browser never makes this call, and containers can't: the check runs only in
the host.

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
  is pinned in the package (`release_keys.py` holds a placeholder), DataLab
  trusts no release, doesn't ask GitHub, and Updates says so. New versions
  are then installed with the installer.
- The release notes are shown as text, never as HTML.

### Release signing

A release's `SHA256SUMS` is signed with the lab's **release key** (Ed25519),
in the release workflow, as `SHA256SUMS.sig` (`datalab/signing.py`,
`scripts/sign-release.py`). Each DataLab trusts only the public keys pinned in
its own package (`datalab/release_keys.py`), and refuses a release with a
missing or bad signature. Since `SHA256SUMS` names every other file by its
checksum, including `requirements.txt` (every dependency by hash) and
`images.json` (every image by digest), the signature covers everything an
update installs. The package's name in `SHA256SUMS` carries its version,
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
the "release" environment, behind its required reviewers, so a workflow run
on any other branch or tag can't use it.

**What it doesn't.** Code merged into `main` and released the normal way;
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
   **required reviewers** (the maintainers), and add the private key as the
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
is published.

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

The launcher (DataLab.app, the Start menu entry) runs `bin/datalab serve`,
so switching `current` is all an update changes in it. If the version
`current` names can't run, the Mac `bin/datalab` falls back to `previous`
and says so; by hand, `versions/<old>/bin/datalab versions --use <old>`
(`Scripts\datalab.exe` on Windows) points it back.

**The real and practice DataLabs share all of this**: the versions, `current`
and `previous`. An update switches both, so it refuses while the other
profile's DataLab is open. Each has its own launcher on Mac, "DataLab" and
"DataLab (practice)", so installing one never replaces the other's. An installer from
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

The uninstaller removes the app, the launcher, the images, and the keychain
entries. It asks separately, showing sizes, whether to delete the data folder.
It never touches export destinations.

## Windows specifics

- Docker Desktop needs WSL2. That means one administrator step and usually a
  restart; the installer resumes where it left off. On Michigan Medicine
  managed machines this may need temporary elevation, the normal JIT process.
- Credentials go in Windows Credential Manager, and paths use
  `%LOCALAPPDATA%`.
- **Updating on Windows is UNTESTED on a real machine.** It follows the
  same steps as on Mac, with the Windows paths (`Scripts\datalab.exe`,
  `bin\datalab.cmd`), the helper started detached, the new version opened in
  a new PowerShell window, and a process check through the Windows API; unit
  tests cover the paths and commands it builds. The side-by-side install and
  the GitHub step in `install.ps1` are untested there too.
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
    `images.json`), with the installers, `requirements.txt` (every
    dependency by hash, then the package by its checksum), a `SHA256SUMS`
    file and its signature, `SHA256SUMS.sig`, made in the protected
    "release" environment (see "Release signing"). The update check offers
    only releases with all of these (see "Which releases are offered"); a
    pre-release is tagged `v0.1.0-alpha.3` and marked so. `constraints.txt`
    (the same versions without hashes) is still published for the Windows
    installer until it moves to `requirements.txt`.
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
  templates without their values. The user pastes it into an email or a
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
