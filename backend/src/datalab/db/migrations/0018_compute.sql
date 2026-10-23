-- The lab's shared DataLab: where a conversation's containers run. "standard"
-- on DataLab's own machine; "high" on the larger one ([server.high_compute]).
-- Chosen when the conversation is made, and kept: its folders live there.
ALTER TABLE conversations ADD COLUMN compute TEXT NOT NULL DEFAULT 'standard'
    CHECK (compute IN ('standard', 'high'));
