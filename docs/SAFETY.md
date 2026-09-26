# DataLab safety promises

Status: **draft** for v1. Under discussion and not yet implemented.

DataLab lets you use an AI agent (OpenAI Codex on U-M GPT) with sensitive IHS
data. This page says what DataLab promises, how it keeps each promise, and how
you can check it.

## Two kinds of sessions

Every conversation is one of two kinds. You choose when you start it, and it
can't change afterwards.

| | 🔒 **Data session** (default) | 🌐 **Research session** |
|---|---|---|
| Oracle database | Yes, read-only | No |
| Attached files | Yes, read-only | Yes, with a warning that they may reach the internet |
| Internet | **No.** U-M GPT only | Yes: web search, papers, package installs |
| Use it for | Analysis, extraction, workflows | Literature, methods, trying new tools |

Files can move **from** a research session **into** a data session, for
example code, notes, or papers. Nothing moves the other way. The only way data
leaves DataLab is when you export it.

**Research helper.** A data-session agent can ask a temporary research helper
to look something up, such as package docs or a paper. You see the exact
question first, and you can edit, approve, or decline it. The helper gets only
that question. It has no conversation history, no files, and no database. It
returns an answer and is then deleted.

## The promises

1. **The AI can't touch anything else on your computer.**
   The AI works in a sealed box. It sees only its own workspace and the files
   or folders you attach, and it can't change the files you attach.

2. **The AI can't change or damage the research database.**
   The AI reaches Oracle only through DataLab, using a read-only account. It
   never sees the database password. DataLab also limits how heavy the AI's
   queries can be.

3. **Sensitive data only goes to approved places.**
   A data session can talk to U-M GPT and DataLab, and nothing else. A
   research session has the internet but no access to the database. A
   question from a data session to the research helper goes out only after
   you approve it.

4. **Your work isn't lost.**
   Conversations, results, and workflow runs are saved and survive restarts.
   Every agent turn is checkpointed, so a mistake can be rolled back. Nothing
   is deleted unless you delete it.

5. **Results leave only when you export them.**
   The AI can prepare files, but only you can export them, to a folder you
   chose such as a local folder or your Dropbox folder. The one exception is a
   workflow you've approved: when it runs, it writes to the destination you
   configured for it.

6. **The shared knowledge base never receives data without your review.**
   The knowledge base is shared with the whole lab through GitHub. Agents can
   suggest edits, but you see the exact change before it's shared. It must
   never contain participant-level data.

7. **You can see everything the AI accessed.**
   Each conversation has a **Data accessed** panel. It lists every query the
   AI ran: which tables, when, how many rows, and where the result went.

8. **You can check all of this.**
   The **Safety check** screen tests these promises live and shows the results.

## Outside AI tools

Other AI tools, such as Claude, can connect to DataLab to use and test it.
They are **not** approved for study data, so what they can reach depends on
which DataLab they connect to:

- The **practice** DataLab has only synthetic data, and you can't attach
  your own files to it. Outside tools can do everything there.
- A **real-data** DataLab doesn't accept outside tools at all in v1.

## What DataLab does *not* promise

- **The AI sees the data you give it.** That is the point, and U-M GPT is the
  approved destination for that data.
- **Exported files are yours to look after.** Once they're in your own folder,
  DataLab no longer controls them.
- **Anything you attach to a research session may reach the internet.**
  The same goes for anything you approve in a research-helper question, so
  read it before you approve.
- **The AI can be wrong.** Check its analysis as you would a colleague's.
- **It can't protect a compromised computer.** If your computer has malware,
  or Docker Desktop has a security flaw, these promises can't hold. Keep your
  OS and Docker Desktop updated.

---

## How each promise is enforced (for maintainers)

Design rule: **fail closed.** If something is misconfigured, the agent loses
access. It never gains it.

### Host isolation

- Each conversation runs in its own Docker container. The container runs as a
  non-root user, drops all Linux capabilities, and has CPU and memory limits.
- Mounts are exactly the conversation's workspace plus any attached items,
  **read-only**. Never mounted: the home folder, the Docker socket, credential
  stores, or another conversation's workspace.
- Model-written code runs only inside containers, never in DataLab's host
  process.
- **The host runs only DataLab's own code.** Scripts from the lab repos, such
  as pipeline tests, custom QC checks, and workflow R steps, run in a
  container with no network. The knowledge-base and workflow checks are part
  of DataLab itself, not files taken from a repo.
- **The browser is part of the boundary.**
  - An app-wide Content Security Policy blocks every network request that
    isn't to DataLab itself. An image link or chart in an agent's answer
    can't send data anywhere.
  - Agent-made HTML reports are previewed in a sandboxed frame with **scripts
    turned off** and a policy that allows no network requests. With scripts
    on, a page could send data out simply by navigating itself to another
    site, and no browser policy can stop that. Interactive reports work once
    you export them and open them yourself.
  - Previewed pages are cleaned first: resource hints (`dns-prefetch`,
    `preconnect`), `<meta>` and `<base>` tags, scripts, and frames are
    removed, since a DNS lookup of a crafted name could carry data out. DNS
    prefetching is also switched off for the whole app. Previews are served
    only into the viewer's frame, never as a page of their own.
  - Workspace files shown in the viewer come from the latest checkpoint,
    never the live folder, so the agent can't swap a file for a link to
    somewhere else on the computer while DataLab reads it. They carry the
    same inert policy, so opening one directly in a tab can't run anything.
  - Exported conversation reports carry the same no-network policy.
- DataLab listens only on `127.0.0.1`, and its API requires the session
  cookie the launcher sets.

### Database safety

- Only DataLab's host process holds the Oracle password, which it reads from
  the OS keychain. The password never appears in a container, in files, or in
  logs.
- **Every connection enables only the read-only roles.** DataLab runs
  `SET ROLE <read-only roles>` before anything else.
  - Why: the shared service account `SVC_IHS_AGENT` holds a role
    (`IHS_2026_ROLE`) that grants UPDATE, DELETE, and ALTER on the live 2026
    tables, plus CREATE privileges. This was checked on 2026-09-26.
  - Effect: with the write role switched off, the session's only system
    privilege is `CREATE SESSION`, and every cohort remains readable, because
    older cohorts are granted to the account directly.
  - Limit: read-only transactions alone would not be enough, since they don't
    block DDL.
  - Follow-up: the DBA is asked to remove the write role from the account, as
    a second layer (see the PRODUCT to-dos).
- Each query runs in a `READ ONLY` transaction. Only `SELECT`/`WITH`
  statements are accepted; the SQL is parsed, not pattern-matched.
- **Guardrails protect the database and the laptop:**
  - an end-to-end deadline that really cancels the query in Oracle;
  - caps on rows and bytes per extraction, which only the user, not the
    agent, can raise;
  - a disk-space check;
  - limits on how many queries and containers run at once.
- Research-session containers get no data-service route and no data mounts.

### Network and credentials

- **The real U-M GPT key never leaves DataLab's host process.** No container
  and no file holds it.
  - Each session's Codex gets a **session token** instead.
  - All model requests go to DataLab's **model relay**, which checks the
    token, validates the request, and only then adds the real key.
- **The relay allows only the request shapes Codex needs.** It refuses:
  - hosted tools (web search, remote MCP servers, code interpreter);
  - URLs the provider would fetch (image or file links);
  - stored or background responses;
  - other endpoints.

  Research sessions are additionally allowed hosted web search. This matters
  because the U-M endpoint *does* run hosted tools if asked, and code in a
  container could ask directly, bypassing Codex's own settings. The spike
  demonstrated this.
- **Agent containers sit on an internal Docker network with no route out.**
  Their only exit is a small **gateway** that holds no secrets:
  - **data sessions:** the gateway forwards only to the relay and to DataLab's
    agent tools;
  - **research sessions:** the gateway also has a forward proxy to the general
    internet, which refuses the host, private networks, and DataLab's tools.
- **DNS.** Containers can resolve only `gateway`. Queries for other names
  never leave the machine; this was verified with a packet capture.
- **Codex settings.** Telemetry, update checks, and history are off. Every
  feature that opens a new channel is switched off explicitly, since most are
  on by default:
  - memories;
  - plugins and apps;
  - browser and computer use;
  - image generation;
  - multi-agent;
  - realtime.

  Hosted web search is off in data sessions too. Codex memories in particular
  would store summaries of past sessions where they could reach other
  sessions.
- **Codex home folders.** Each session gets its own. It contains
  conversation content, so it is treated as study data and never shared
  across sessions.
- **Pinned Codex version.** It is fixed in the agent image and upgraded
  deliberately.
- **No internet package installs.** Data sessions can't install packages from
  the internet, so the agent image ships with a curated R/Python toolkit.

### Research helper

- Data sessions get an `ask_research_helper` tool. Calling it pauses the agent
  and shows the user an approval card with the exact question text, which the
  user can edit.
- Nothing is sent until the user approves. Declining returns a "declined"
  result to the agent.
- When approved, DataLab starts a temporary research-session container. It gets
  only the approved text: no history, no files, no data-service route.
- The helper returns text and, optionally, files, which count as research →
  data transfers. The container is then destroyed.
- There is no auto-approval in v1. Every question gets a human decision, and
  every decision is logged in the conversation.

### Data access log

- The data service records every query in the conversation's **Data
  accessed** panel and in a local append-only audit log. Each entry has the
  time, session, tables and schemas referenced, row count, a SQL fingerprint,
  and the result's file location.
- The log is metadata only. It never contains result values.
- Workflow runs log to the same audit log, attributed to the run.

### Shared knowledge base

- The knowledge base is a git repository synced with GitHub. It is mounted
  **read-only** into every container, in both session types.
- Agents propose edits as diffs. Only a person saves and pushes, and they see
  the exact diff first.
- Git operations run in DataLab's host process with the user's GitHub token,
  which is stored in the OS keychain. The token never enters a container.
- Before sharing, DataLab scans the diff for things that look like row-level
  data, such as participant IDs, per-person dates, or pasted tables, and warns
  the user. The scan is an aid; the human review is the control.
- Because research sessions read the knowledge base too, an edit that came
  from a data session reaches them only after a person has reviewed and saved
  it.

### Outside AI connectors

- DataLab exposes its features to outside tools through an MCP server and a
  `datalab` CLI. Both use the same API as the UI, and every request carries a
  connector token whose scope the host app enforces on every route.
- **v1: practice profile only.**
  - The practice profile has its own data folder and uses only the synthetic
    backend.
  - It can't attach host files. It can't use real export destinations, only a
    disposable practice folder.
  - It has no write access to the lab repos.

  A practice instance therefore can't hold real data, even by accident.
- **The practice profile checks it's really talking to the synthetic
  database.** Every connection looks for a marker table that exists only
  there. Without it, no query runs, even if something else answers on the
  practice port, such as an SSH tunnel to the real server.
- **The synthetic database is reachable only from this computer.** Its port is
  bound to `127.0.0.1`, since its dev passwords are public. Its setup scripts
  refuse to run against anything that isn't the local Oracle Database Free
  container.
- **Real-profile connectors are deferred** until the exact permitted fields,
  operations, and error handling are specified. Even "metadata" can leak
  through error messages or SQL in workflow files.

### Work preservation and storage

- Conversations, run history, and settings live in one local database in
  DataLab's data folder.
- After each turn, the workspace is checkpointed to a host-side store the
  agent can't write to. The container is paused while this happens, and
  links in the workspace are never followed, so the agent can't trick
  DataLab into copying a file from elsewhere on the computer. Links are kept
  as links.
- A rollback never deletes anything the checkpoint taken just before it
  couldn't save (a very large file, a pipe): it's left as it is, and the
  chat says so.
- **Rollback restores workspace files** to how they were after a chosen turn.
  The conversation isn't rewound; the agent is told the files were restored.
  Very large files aren't checkpointed, and the rollback screen lists them.
- Before an app update changes the database, DataLab **backs it up**. Rolling
  back to the previous version restores that backup.
- DataLab writes only to:
  - **its data folder**, which is internal (see
    [DISTRIBUTION.md](DISTRIBUTION.md) for the layout);
  - **export destinations** that the user configures. Each export goes into
    its own dated subfolder with a manifest recording what it is, when it was
    made, and which session or workflow produced it.
- Nothing is deleted automatically. A Storage view in Settings shows disk
  use and offers one-click cleanup of old conversations and runs.
- PHI is permitted on these machines, which are PHI-approved. Cleanup exists
  for tidiness, not as a safety control.

### Safety check

The Safety check runs at startup and on demand. It never attempts to write to
Oracle.

| Check | How it's tested |
|---|---|
| Data session can't reach the internet | A request from inside a container to an outside host fails, and an outside DNS name doesn't resolve |
| Hosted tools are refused | A web-search, remote-MCP, or image-URL request sent straight to the relay from a data container is rejected |
| U-M GPT is reachable | The model list loads from inside a container |
| No key in containers | Container environment and config have no U-M GPT key or Oracle password |
| No unexpected mounts | `docker inspect` mounts match the expected set |
| Container is locked down | Non-root, capabilities dropped, not privileged, no Docker socket |
| Research session can't reach data | The data service is unreachable from a research container |
| Helper gets only the approved text | A helper container's mounts and inputs contain only the approved question |
| Database access is read-only | After connecting, the session's enabled roles are exactly the read-only set, its system privileges are exactly `CREATE SESSION`, and it has no non-SELECT privileges on any IHS schema |
| Right agent image | The image digest matches the pinned release |
| Agent HTML can't phone home | A test report that tries to run a script, load an outside image, refresh to an outside URL, or submit a form sends nothing in the preview |
