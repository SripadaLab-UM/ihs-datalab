-- The Express switch, per conversation (off by default): quick answers, low
-- effort, no plans or confirmations. Never on together with rigor_review.
ALTER TABLE conversations ADD COLUMN express INTEGER NOT NULL DEFAULT 0;
