-- What a query was run for. `session_id` stays the owner's id: a
-- conversation id, a SQL Playground id (pg_…), or a workflow run id (run_…).
-- Every query before this came from a conversation.
ALTER TABLE queries ADD COLUMN origin TEXT NOT NULL DEFAULT 'conversation'
    CHECK (origin IN ('conversation', 'playground', 'run'));

CREATE INDEX queries_by_origin ON queries (origin, session_id, started_at);
