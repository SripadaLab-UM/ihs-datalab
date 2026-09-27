-- The knowledge base (milestone 5): each conversation's proposed edits, what
-- its copy at /work/kb is compared against, and the lab repos' sync state.

-- The commit each conversation's /work/kb is compared against. It starts as
-- the commit the copy was made from, and moves on each time a proposal is
-- saved or discarded, so the next proposal holds only what's new.
CREATE TABLE kb_bases (
    conversation_id  TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    base             TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- Proposed edits. The agent's files stay in the conversation's checkpoint
-- store (by content hash); a person's edits are kept here.
CREATE TABLE kb_proposals (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    turn             INTEGER NOT NULL,
    checkpoint       INTEGER NOT NULL,
    base             TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN (
        'open', 'superseded', 'withdrawn', 'rejected', 'saving', 'saved',
        'conflict', 'check_failed', 'failed'
    )),
    files_json       TEXT NOT NULL,
    refused_json     TEXT NOT NULL,
    edits_json       TEXT NOT NULL DEFAULT '{}',
    result_json      TEXT NOT NULL DEFAULT '{}',
    commit_sha       TEXT,
    decided_by       TEXT
);
CREATE INDEX kb_proposals_by_conversation ON kb_proposals (conversation_id, created_at);

-- Each lab repo's clone: GitHub's main when last synced, and the last error.
CREATE TABLE repo_sync (
    repo       TEXT PRIMARY KEY,
    head       TEXT,
    synced_at  TEXT,
    error      TEXT,
    error_at   TEXT
);
