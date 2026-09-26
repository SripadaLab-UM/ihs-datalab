-- Conversations and everything that happened in them.
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('data', 'research')),
    mode        TEXT NOT NULL,
    title       TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- An append-only log per conversation: user messages, answer text, reasoning,
-- commands, tool calls, and turn status. `seq` lets the browser pick up where
-- it left off after a reconnect.
CREATE TABLE events (
    conversation_id  TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    type             TEXT NOT NULL,
    data_json        TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
