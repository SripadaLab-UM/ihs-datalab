# For coding agents working on DataLab

This file is for AI coding agents (Codex, Claude Code, …) changing this
repository. It is **not** the agent that runs inside DataLab: that agent's
instructions are `images/agent/AGENTS.md`, baked into the agent image. Don't
edit that one for repo rules, or this one for the in-container agent.

## What DataLab is

A local app that lets Intern Health Study researchers use an AI agent
(OpenAI Codex on U-M GPT) on sensitive study data, safely. One Python host
process (FastAPI + SQLite) serves a React UI, runs a read-only Oracle data
service, a model relay that holds the only key, and one sealed Docker
container per conversation behind a gateway. Around it: the SQL Playground,
reviewed workflows and the `ihsDataR` pipelines, and a shared knowledge base
in GitHub. A practice profile runs the same app on a synthetic database.

## Authoritative documents

| Document | Use it for |
|---|---|
| [PRODUCT.md](PRODUCT.md) | The product contract: decisions, the modes' policy matrix, scope, and v1 readiness. Implemented vs planned is marked there |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it's built, and where each responsibility lives in the code |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety promises and how each is enforced and tested. A change that touches one needs its test |
| [docs/guide/](docs/guide/README.md) | The user Help, bundled into the app (`frontend/src/lib/guide.ts`) and its tooltips. The only copy of user-facing words |
| docs/WORKSPACE.md, KNOWLEDGE_BASE.md, WORKFLOWS.md, DISTRIBUTION.md | Detail for one area each |

If the code and a document disagree, the code is what ships: fix the
document in the same change. Don't start new plan documents; update these.

## Rules

- **One git worktree per branch.** Never share a checkout with another
  session. Name the worktree, branch and last commit in every handoff.
- **Every user-facing change** updates its Help page in `docs/guide/` (exact
  button labels, as the frontend has them) and its status in PRODUCT.md.
- **Before a new feature**, put a short design note in the PR: the user
  need, the existing mechanism it reuses, the new complexity it adds, and
  the end-to-end acceptance test.
- **Tests at a boundary use the real protocol's shapes**, taken from a live
  run, not a guess: for example
  `test_the_live_app_server_shape_of_a_failed_query_keeps_its_reason` in
  `backend/tests/test_runtime.py` (Codex app-server notifications).
- **Study data**: Claude and other outside AI tools work only with the
  practice profile and synthetic data. Never open a real DataLab data
  folder, and never query the real database.
- **Public repo**: no hostnames, IPs, emails, credentials or other
  environment-specific details.
- Formatting: no prettier (the repo keeps its own formatting). Don't run
  `npx tsc -b`.
- Line endings: `.gitattributes` keeps text files LF on every platform (a
  Windows checkout too). A test that reads a source or Help file as text
  still normalizes `\r\n`, for checkouts made before it.

## Checks

```sh
cd frontend && npm ci && npx vitest run && npm run build   # build type-checks and bundles Help
cd backend && uv run ruff check . && PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring uv run pytest -q
```

CI (`.github/workflows/ci.yml`) also runs pyright, the Windows tests, the
upgrade-and-rollback test, and the synthetic-Oracle integration and
adversarial Safety check jobs.
