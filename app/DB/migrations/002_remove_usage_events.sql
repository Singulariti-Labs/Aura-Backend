-- The per-call usage ledger is intentionally deferred. This keeps databases
-- that applied an earlier version of 001_subscriptions aligned with the
-- current schema; on fresh databases the statement is a safe no-op.
DROP TABLE IF EXISTS usage_events;
