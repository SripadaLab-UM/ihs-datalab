# Installing, updating, and running DataLab

Status: **draft** for v1. This is a proposal under discussion and has not been
implemented.

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
- If anything fails, DataLab stays on the previous version. It is kept, so
  rolling back is instant.
- Updates never touch the data folder. It stays separate from the app.

This replaces the prototype's approach of fast-forwarding a git checkout,
which needed seven safety gates. There is no checkout on users' machines to
protect.

## Where DataLab keeps things

**One data folder** holds everything DataLab owns. On Mac it is
`~/Library/Application Support/DataLab`, and on Windows
`%LOCALAPPDATA%\DataLab`.

```
datalab.sqlite         conversations, workflow runs, settings
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
  - run all checks;
  - build the app with the frontend included;
  - build and push the agent and gateway images;
  - publish a release that lists the exact image digests.
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
