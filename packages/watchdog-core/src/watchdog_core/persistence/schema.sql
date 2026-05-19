-- wk-watchdog initial schema (migration version 1)
-- =====================================================================
-- The webhook_outbox table implements the TRANSACTIONAL OUTBOX pattern
-- (Richardson, "Microservices Patterns", Manning, 2019, Ch. 3.2).
--
-- Why outbox is the correct shape here
-- ------------------------------------
-- When the watchdog raises an alert, two side effects must remain atomic
-- with the database write to preserve correctness across crashes:
--   (1) Persist the alert row.
--   (2) Send a webhook notification to the configured receiver.
-- A naive implementation performs (2) synchronously after committing
-- (1). A process crash, network blip, or container restart between
-- those steps silently loses the notification — the classic
-- DUAL-WRITE problem.
--
-- The outbox decouples the two writes: a row is inserted into
-- webhook_outbox inside the SAME transaction as the alert. A separate
-- dispatcher polls webhook_outbox and performs the HTTP call with
-- retry + dead-letter semantics. Combined with HTTP-side idempotency
-- keys at the receiver, this delivers at-least-once with no message
-- loss and observable redelivery.
--
-- For SQLite specifically, WAL mode (set via PRAGMA in migrations.py)
-- ensures the dispatcher's reads of pending rows don't block the
-- application's writes.
-- =====================================================================

CREATE TABLE IF NOT EXISTS log_events (
    id              TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    service         TEXT NOT NULL,
    level           TEXT NOT NULL
                        CHECK (level IN ('DEBUG','INFO','WARN','ERROR','CRITICAL')),
    message         TEXT NOT NULL,
    message_hash    TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_log_events_service_ts
    ON log_events(service, ts);

CREATE INDEX IF NOT EXISTS idx_log_events_service_level_ts
    ON log_events(service, level, ts);

CREATE INDEX IF NOT EXISTS idx_log_events_dedupe
    ON log_events(service, message_hash, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id              TEXT PRIMARY KEY,
    service         TEXT NOT NULL,
    level           TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    event_count     INTEGER NOT NULL,
    baseline_mean   REAL NOT NULL,
    baseline_stddev REAL NOT NULL,
    z_score         REAL NOT NULL,
    severity        TEXT NOT NULL
                        CHECK (severity IN ('low','medium','high','critical')),
    reasoning       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    dispatched_at   TEXT,
    webhook_status  TEXT NOT NULL DEFAULT 'pending'
                        CHECK (webhook_status IN ('pending','delivered','failed','dead_letter'))
);

CREATE INDEX IF NOT EXISTS idx_alerts_service_ts
    ON alerts(service, window_end);

CREATE INDEX IF NOT EXISTS idx_alerts_webhook_status
    ON alerts(webhook_status) WHERE webhook_status = 'pending';

CREATE TABLE IF NOT EXISTS webhook_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        TEXT NOT NULL REFERENCES alerts(id),
    payload_json    TEXT NOT NULL,
    enqueued_at     TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','delivered','failed','dead_letter'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_outbox_status_next
    ON webhook_outbox(status, next_attempt_at)
    WHERE status IN ('pending','failed');
