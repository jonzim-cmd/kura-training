-- Events table: append-only, immutable, the core of the system.
-- Every training session, meal, metric, note — everything is an event.

CREATE TABLE IF NOT EXISTS events (
    -- UUIDv7: time-sortable, globally unique
    id              UUID PRIMARY KEY,
    -- Owner of this event
    user_id         UUID NOT NULL,
    -- When the event happened (agent/user-reported time)
    timestamp       TIMESTAMPTZ NOT NULL,
    -- Free-form event type (NOT an enum — new types emerge from usage)
    event_type      TEXT NOT NULL,
    -- Event payload (structure depends on event_type)
    data            JSONB NOT NULL DEFAULT '{}',
    -- Metadata about event source
    metadata        JSONB NOT NULL DEFAULT '{}',
    -- Server-side creation time (for auditing, not business logic)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotency: reject duplicate writes
-- Composite key: same user + same idempotency key = duplicate
CREATE UNIQUE INDEX idx_events_idempotency ON events (user_id, (metadata->>'idempotency_key'));

-- Primary query pattern: events for a user, ordered by time
CREATE INDEX idx_events_user_timestamp ON events (user_id, timestamp DESC);

-- Query by event type (e.g. "all set.logged events")
CREATE INDEX idx_events_user_type ON events (user_id, event_type, timestamp DESC);

-- GIN index on data for flexible JSONB queries
CREATE INDEX idx_events_data ON events USING GIN (data);

-- Immutability: REVOKE UPDATE and DELETE on events table.
-- Events are append-only. Corrections are compensating events.
-- The app_writer role can only INSERT.
REVOKE UPDATE, DELETE ON events FROM app_writer;
GRANT INSERT, SELECT ON events TO app_writer;
GRANT SELECT ON events TO app_reader;

-- Row Level Security: every query is filtered by user_id.
-- Defense in depth — even if app code has a bug, RLS prevents cross-user access.
ALTER TABLE events ENABLE ROW LEVEL SECURITY;

-- Policy: users can only see their own events.
-- The user_id is set via a session variable (SET LOCAL kura.current_user_id = '...')
CREATE POLICY events_user_isolation ON events
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

-- Policy: writers can only insert events for themselves
CREATE POLICY events_user_insert ON events
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

-- Audit log: track all API access (separate from events)
CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Who made the request (API key hash, not the key itself)
    actor_key_hash  TEXT,
    -- What user's data was accessed
    user_id         UUID,
    -- What action was performed
    action          TEXT NOT NULL,
    -- Request details
    request_id      TEXT,
    ip_address      INET,
    -- Additional context
    details         JSONB DEFAULT '{}'
);

CREATE INDEX idx_audit_log_user ON audit_log (user_id, timestamp DESC);
CREATE INDEX idx_audit_log_actor ON audit_log (actor_key_hash, timestamp DESC);
