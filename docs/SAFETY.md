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
- Workflow R steps run in a separate container with no network.

### Database safety

- Only DataLab's host process holds the Oracle password, which it reads from
  the OS keychain. The password never appears in a container, in files, or in
  logs.
- The Oracle account has SELECT-only grants. This is the real guarantee;
  everything else is a second layer.
- Each query runs in a `READ ONLY` transaction with a call timeout. Only
  `SELECT`/`WITH` statements are accepted.
- Previews are row-capped, and few queries may run at once, so the production
  database can't be overloaded.
- Research-session containers get no data-service route and no data mounts.

### Network and credentials

- Agent containers sit on an internal Docker network with no route out. The
  only exit is one small **gateway** container. The gateway:
  - adds the U-M GPT key to model requests, so **no container ever holds the
    key** and Codex gets a dummy value;
  - in **data sessions**, allows only U-M GPT and DataLab's data service, and
    refuses everything else, including DNS lookups for other hosts;
  - in **research sessions**, also allows the general internet, but never the
    data service.
- Codex telemetry and update checks are off. Hosted web search is off in data
  sessions, because search queries leave the approved boundary.
- Codex **memories** are off. Otherwise Codex would write summaries of past
  sessions to disk, outside review, where they could reach other sessions.
  Each session gets its own Codex home folder; none is ever shared across
  sessions.
- Other Codex features that open new channels are off in data sessions:
  plugins/apps, browser and computer use, and web search.
- The Codex CLI version is pinned in the agent image and upgraded
  deliberately.
- Data sessions can't install packages from the internet. The agent image
  ships with a curated R/Python toolkit.

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

### Work preservation and storage

- Conversations, run history, and settings live in one local database in
  DataLab's data folder.
- After each turn, the workspace is checkpointed to a host-side store the
  agent can't write to. Rollback restores from that store.
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
| Data session can't reach the internet | A request from inside a container to an outside host fails |
| U-M GPT is reachable | The model list loads from inside a container |
| No key in containers | Container environment and config have no U-M GPT key or Oracle password |
| No unexpected mounts | `docker inspect` mounts match the expected set |
| Container is locked down | Non-root, capabilities dropped, not privileged, no Docker socket |
| Research session can't reach data | The data service is unreachable from a research container |
| Helper gets only the approved text | A helper container's mounts and inputs contain only the approved question |
| Database access is read-only | Account privileges contain no write grants, and the session is read-only |
| Right agent image | The image digest matches the pinned release |
