-- DRAFT for backend/src/datalab/db/migrations/0008_workflow_runs.sql (spike
-- only; not applied by the app). Checked against 0001-0005 only, by
-- check_migration.py; 0006 (queries.origin, wave 0) wasn't included.
--
-- Workflow runs. The run folder (runs/<id>/) holds the files: the kept
-- extracts, every step's outputs and result.json, logs, and record.json, a
-- copy of everything below so a run folder explains itself. These tables are
-- what the Workflows tab lists and what Run again, Replay and delivery read.
--
-- Local study data, like `queries`: SQL text and bind values may contain
-- participant identifiers. The audit log keeps metadata only.

CREATE TABLE workflow_runs (
    id                 TEXT PRIMARY KEY,        -- run_20260927T110059_4c7e20
    workflow_name      TEXT NOT NULL,
    mode               TEXT NOT NULL CHECK (mode IN ('run', 'run_again', 'replay')),
    of_run             TEXT REFERENCES workflow_runs (id),  -- run_again, replay
    set_id             TEXT,                    -- "Run a set": runs started together
    status             TEXT NOT NULL CHECK (status IN
                           ('running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    started_by         TEXT NOT NULL,           -- the person's git name and email
    -- The definition: exact commit, and the file itself so a replay needs no repo.
    repo_commit        TEXT NOT NULL,           -- ihs-pipelines commit
    workflow_path      TEXT NOT NULL,           -- workflows/<name>.yaml
    workflow_blob      TEXT NOT NULL,           -- git blob id: the same on Mac and Windows
    workflow_text      TEXT NOT NULL,
    pipelines_json     TEXT NOT NULL DEFAULT '[]',  -- [{name, tree, library_sha256}]
    -- The environment.
    image_ref          TEXT NOT NULL,           -- ghcr.io/…/agent:<version>
    image_digest       TEXT NOT NULL,           -- the platform's own manifest digest
    image_platform     TEXT NOT NULL,           -- linux/arm64 or linux/amd64
    r_packages_sha256  TEXT NOT NULL,           -- fingerprint of installed.packages()
    runner_version     TEXT NOT NULL,           -- DataLab version and step wrapper sha256
    -- What it was asked to do.
    params_json        TEXT NOT NULL,
    seed               INTEGER NOT NULL,        -- step seeds derive from this and the step id
    reads_json         TEXT NOT NULL,           -- the declared Oracle objects
    run_dir            TEXT NOT NULL,           -- relative to the data folder
    inputs_kept        INTEGER NOT NULL DEFAULT 1,  -- 0 once Storage cleanup removed them
    reproduced         INTEGER,                 -- replay only: 1 if every output matched
    message            TEXT
);

CREATE INDEX workflow_runs_by_workflow ON workflow_runs (workflow_name, started_at);
CREATE INDEX workflow_runs_by_set ON workflow_runs (set_id);

CREATE TABLE workflow_run_steps (
    run_id        TEXT NOT NULL REFERENCES workflow_runs (id) ON DELETE CASCADE,
    step_id       TEXT NOT NULL,
    position      INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('sql', 'r', 'pipeline', 'qc_builtin', 'qc_custom')),
    status        TEXT NOT NULL CHECK (status IN
                      ('pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')),
    started_at    TEXT,
    finished_at   TEXT,
    seed          INTEGER,
    -- SQL steps. The data service's own row (queries.id, session_id = the run
    -- id) has the same; it's copied here because Storage cleanup may remove it.
    query_id      TEXT,
    sql_text      TEXT,
    binds_json    TEXT,
    -- Container steps.
    exit_code     INTEGER,
    elapsed_ms    INTEGER,
    inputs_json   TEXT NOT NULL DEFAULT '{}',  -- {name: {step, file, sha256}}
    outputs_json  TEXT NOT NULL DEFAULT '{}',  -- {name: {file, sha256, bytes, rows, columns}}
    result_json   TEXT,                        -- result.json, or the built-in checks
    PRIMARY KEY (run_id, step_id)
);

CREATE TABLE workflow_run_deliveries (
    id                TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES workflow_runs (id) ON DELETE CASCADE,
    destination_key   TEXT NOT NULL,           -- as the workflow names it
    destination_id    TEXT REFERENCES export_destinations (id) ON DELETE SET NULL,
    destination_path  TEXT NOT NULL,           -- the folder it was then
    folder            TEXT NOT NULL,           -- the dated folder export() made
    files_json        TEXT NOT NULL,           -- [{path, bytes, sha256}], as in the manifest
    manifest_sha256   TEXT NOT NULL,
    delivered_at      TEXT NOT NULL
);

CREATE INDEX workflow_run_deliveries_by_run ON workflow_run_deliveries (run_id);

-- A workflow file names its destination (`destination: dropbox-ihs-2025`);
-- each computer maps that key to a folder the person chose. Destination names
-- are labels and needn't be unique, so the mapping gets its own column.
ALTER TABLE export_destinations ADD COLUMN key TEXT;
CREATE UNIQUE INDEX export_destinations_by_key ON export_destinations (key) WHERE key IS NOT NULL;
