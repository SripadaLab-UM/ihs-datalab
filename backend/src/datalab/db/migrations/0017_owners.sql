-- Who owns what, for the lab's shared DataLab (api/access.py). Everything
-- before this belongs to the local user (id 1): a person's own computer.
-- (No REFERENCES: SQLite can't add one with a default, with foreign keys on.)

ALTER TABLE conversations ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE workflow_runs ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kb_edits ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE pipeline_edits ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1;
-- Who ran it: a conversation's, a Playground's or a workflow run's query.
ALTER TABLE queries ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1;

CREATE INDEX conversations_owner ON conversations (owner_id);
CREATE INDEX workflow_runs_owner ON workflow_runs (owner_id);
CREATE INDEX kb_edits_owner ON kb_edits (owner_id);
CREATE INDEX pipeline_edits_owner ON pipeline_edits (owner_id);
CREATE INDEX queries_user ON queries (user_id);

-- One open draft of a page per person (it was per page: one person per computer).
DROP INDEX kb_edits_open;
CREATE UNIQUE INDEX kb_edits_open ON kb_edits (owner_id, path) WHERE status NOT IN ('saved', 'discarded');
