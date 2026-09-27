-- What a query was run for. `session_id` stays the owner's id: a
-- conversation id, a SQL Playground id (pg_…), or a workflow run id (run_…).
ALTER TABLE queries ADD COLUMN origin TEXT NOT NULL DEFAULT 'conversation'
    CHECK (origin IN ('conversation', 'playground', 'run'));

-- Existing queries get their origin from the owner id. Before this they all
-- came from conversations, except in a database rolled back from a newer
-- layout (see docs/DISTRIBUTION.md): the rollback keeps every query but not
-- this column, so upgrading again must put Playground and run queries back
-- under their owners. (substr, not LIKE: "_" is a LIKE wildcard.)
UPDATE queries SET origin = CASE
    WHEN substr(session_id, 1, 3) = 'pg_' THEN 'playground'
    WHEN substr(session_id, 1, 4) = 'run_' THEN 'run'
    ELSE 'conversation'
END;

CREATE INDEX queries_by_origin ON queries (origin, session_id, started_at);
