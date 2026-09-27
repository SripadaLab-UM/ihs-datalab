-- Pipelines (milestone 6): changes Data engineering conversations propose to
-- the pipelines repo (ihs-pipelines: the ihsDataR package and the workflow
-- files), what each conversation's /work/pipelines is compared against, and
-- each run of the package's tests. The repo's sync state is `repo_sync`
-- (0007), as "pipelines".

-- As kb_bases: the commit each conversation's copy is compared against. It
-- starts as the commit the copy was made from, and moves on each time a
-- proposal is saved or discarded.
CREATE TABLE pipeline_bases (
    conversation_id  TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    base             TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- As kb_proposals. The agent's files stay in the conversation's checkpoint
-- store (by content hash). `proposal_commit` is the base with the agent's
-- files, never pushed; `tree` is its tree: what the tests run on, and what
-- Save & share shares. `commit_sha` is what was pushed, once saved.
CREATE TABLE pipeline_proposals (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    turn             INTEGER NOT NULL,
    checkpoint       INTEGER NOT NULL,
    base             TEXT NOT NULL,
    proposal_commit  TEXT NOT NULL,
    tree             TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN (
        'open', 'superseded', 'withdrawn', 'rejected', 'saving', 'saved',
        'conflict', 'check_failed', 'tests_failed', 'failed'
    )),
    files_json       TEXT NOT NULL,
    refused_json     TEXT NOT NULL,
    result_json      TEXT NOT NULL DEFAULT '{}',
    commit_sha       TEXT,
    decided_by       TEXT
);
CREATE INDEX pipeline_proposals_by_conversation ON pipeline_proposals (conversation_id, created_at);

-- Each run of the package's tests (testthat, in a no-network container), by
-- the git tree it ran on: a proposal's, or a rebased commit's before a push.
-- The log is a file in <data folder>/pipeline-tests/<id>.log.
CREATE TABLE pipeline_tests (
    id            TEXT PRIMARY KEY,
    tree          TEXT NOT NULL,
    proposal_id   TEXT REFERENCES pipeline_proposals (id) ON DELETE SET NULL,
    commit_sha    TEXT,                     -- the commit it ran on, when there was one
    status        TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed', 'error')),
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    image_digest  TEXT,
    summary_json  TEXT NOT NULL DEFAULT '{}',  -- {tests, passed, failed, skipped, errors, warnings, files: [...]}
    message       TEXT
);
CREATE INDEX pipeline_tests_by_tree ON pipeline_tests (tree, started_at);
