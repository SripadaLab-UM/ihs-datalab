# IHS DataLab

A safe local workspace for using an AI agent (OpenAI Codex on U-M GPT) with
Intern Health Study data.

> **Status:** v1 is in development. Colleagues should keep using the
> prototype until v1 is released.

## What it promises

The AI works in a sealed container. It can't touch anything else on your
computer, can't change the research database, and can only talk to approved
places. You can check all of this in the app. The details are in
[docs/SAFETY.md](docs/SAFETY.md).

## Documentation

| Document | What it covers |
|---|---|
| [PRODUCT.md](PRODUCT.md) | What v1 is, the decisions made, scope, and to-dos |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety promises and how each is enforced |
| [docs/WORKSPACE.md](docs/WORKSPACE.md) | Modes, skills, tools, and the screens |
| [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) | The shared lab knowledge base |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | Repeatable workflows and pipelines |
| [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) | Installing, updating, and where data lives |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it's built |

## Repository layout

```
backend/     the DataLab app (Python): data service, agent tools, API
synthetic/   a fake IHS database for development, tests, and practice mode
spikes/      throwaway proofs of concept kept as design evidence
docs/        design and safety documentation
```

## Development

The backend uses [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
uv run pytest
```

Check what a database session is allowed to do. This needs `settings.toml` in
the profile's data folder and the password in the keychain:

```bash
uv run datalab db-check
```

Use the synthetic database instead of the real one with
`--profile practice` (see [synthetic/README.md](synthetic/README.md)).
