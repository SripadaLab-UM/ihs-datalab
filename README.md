# IHS DataLab

A safe local workspace for using an AI agent (OpenAI Codex on U-M GPT) with
Intern Health Study data.

> **Status:** pre-release (latest: 0.3.0b1). v1.0.0 is the release for
> colleagues; what's done and what's left is in
> [PRODUCT.md](PRODUCT.md), "v1 readiness".

## Installing

**Use the lab's install page.** It gives you one command to paste into
Terminal (Mac) or PowerShell (Windows), with the lab's settings already
included; the installer then walks you through the rest. Ask the DataLab
maintainer for the link.

DataLab needs **Docker Desktop**. On a Mac the installer finds it (even if its
`docker` command isn't on your PATH) and starts it; if it's missing, it offers
to download Docker's official Docker Desktop for your Mac, checks it's signed
by Docker Inc, installs it, and waits while you finish Docker's first-run
window (its agreement, which you accept yourself, and its settings). On
Windows the installer sets up WSL and Docker Desktop; if that needs an
administrator, it says so first, asks once, and carries on by itself after
the restart.

The installer asks for your U-M GPT key (and, for the real DataLab, the
database password if your lab uses one) and saves them in your computer's
keychain. For the real DataLab it then offers the GitHub sign-in for the
lab's knowledge base and pipelines. At the end it says where everything
went.

There are two DataLabs, each installed on its own: the **real** one, and
**practice**, which has only made-up data and needs no database password,
VPN or GitHub account; the installer sets up its synthetic database.

**Where to find it.** Real and practice each get their own app, with their
own icon: **DataLab** (U-M's Block M over "IHS", Maize on Blue, with an AI
spark) and **DataLab (practice)** (the same icon inverted, Blue on Maize).

- **Mac:** the app is in **/Applications**, or in your own Applications
  folder (`~/Applications`) if your account can't add to /Applications
  without an administrator. There's a shortcut to it on your **Desktop**, and
  Spotlight finds it (Cmd-Space, type DataLab). The installer offers to show
  it in Finder and to open it.
- **Windows:** a shortcut on your **Desktop** and an entry in the **Start
  menu**.

The app and its shortcuts open whichever version is in use, so they keep
working after DataLab updates itself (Settings & Safety → Updates).

**To remove DataLab**, run `uninstall-macos.sh` or `uninstall-windows.ps1`
(from the same release). It removes the apps and their shortcuts too, asks
before deleting DataLab's data folder, and never touches your export folders.

### Manual install (for maintainers)

The install page runs the same installers that each GitHub release carries.
To install by hand, download into one folder from a release:

- the `datalab-<version>-py3-none-any.whl` package (for example
  `datalab-0.3.0b1-py3-none-any.whl`);
- `requirements.txt` (every dependency, pinned by hash);
- the installer for your computer (`install-macos.sh` or
  `install-windows.ps1`);
- for the real DataLab, the lab's settings file, from the DataLab
  maintainer. It isn't published.

**Mac**, in Terminal, in that folder:

```bash
sh install-macos.sh --package datalab-0.3.0b1-py3-none-any.whl --settings lab-settings.toml
```

**Windows**, in PowerShell, in that folder (it uses the one `datalab-…whl`
beside it, or name it with `-Package <file>`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install-windows.ps1 -Settings lab-settings.toml
```

For the practice DataLab, add `--profile practice` on Mac (`-Practice` on
Windows); it needs no settings file. If a real install finished without the
lab's settings, run `datalab setup --settings <file>` later.

## What it promises

The AI works in a sealed container. It can't touch anything else on your
computer, can't change the research database, and can only talk to approved
places. You can check all of this in the app (Settings & Safety → Safety).
The details are in [docs/SAFETY.md](docs/SAFETY.md).

## Documentation

| Document | What it covers |
|---|---|
| [PRODUCT.md](PRODUCT.md) | The product contract: decisions, modes and their policy, scope, and v1 readiness |
| [docs/guide/](docs/guide/README.md) | The user guide, which is also DataLab's in-app Help |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety promises and how each is enforced |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it's built |
| [docs/WORKSPACE.md](docs/WORKSPACE.md) | Modes, skills, tools, and the screens |
| [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) | The shared lab knowledge base |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | Repeatable workflows and pipelines |
| [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) | Installing, updating, and where data lives |
| [AGENTS.md](AGENTS.md) | For coding agents working on this repository |

## Repository layout

```
backend/     the DataLab app (Python): data service, agent tools, API
frontend/    the web UI (React)
images/      the agent image, and a small probe image for CI
installer/   Mac and Windows install and uninstall scripts
branding/    the Block M and IHS marks, and the script that makes the favicons and app icons
synthetic/   db.sh and the README of the fake IHS database (its generator is backend/src/datalab/practice_db)
evals/       the scientific evaluation set and its results
spikes/      throwaway proofs of concept kept as design evidence
docs/        design, safety and user documentation
```

## Development

The backend uses [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
cd backend
uv sync
uv run pytest
```

The frontend:

```bash
cd frontend
npm ci
npx vitest run
npm run build
```

Check what a database session is allowed to do. This needs `settings.toml` in
the profile's data folder and the password in the keychain:

```bash
uv run datalab db-check
```

Use the synthetic database instead of the real one with
`--profile practice` (see [synthetic/README.md](synthetic/README.md)).
Before changing anything, read [AGENTS.md](AGENTS.md).
