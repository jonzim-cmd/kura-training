-- Projections & Background Jobs infrastructure
-- Workers process events into read-optimized projections.
-- Job queue uses PostgreSQL SKIP LOCKED + LISTEN/NOTIFY (no external broker).

-- ────────────────────────────────────────────
-- background_jobs: PostgreSQL-native job queue
-- ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS background_jobs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    job_type        TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'dead')),
    priority        INT NOT NULL DEFAULT 0,
    attempt         INT NOT NULL DEFAULT 0,
    max_retries     INT NOT NULL DEFAULT 3,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Worker poll query: grab next pending job (SKIP LOCKED avoids contention)
CREATE INDEX idx_jobs_pending ON background_jobs (scheduled_for, priority DESC, id)
    WHERE status = 'pending';

-- Lookup jobs by user (for debugging/monitoring)
CREATE INDEX idx_jobs_user ON background_jobs (user_id, created_at DESC);

-- No RLS on background_jobs — workers process all users' jobs.
-- Access is controlled by the app_worker role grants below.

-- ────────────────────────────────────────────
-- projections: pre-computed read models
-- ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS projections (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id),
    projection_type     TEXT NOT NULL,
    key                 TEXT NOT NULL,
    data                JSONB NOT NULL DEFAULT '{}',
    version             BIGINT NOT NULL DEFAULT 1,
    last_event_id       UUID REFERENCES events(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One projection per (user, type, key) — e.g. (user_123, exercise_progression, squat)
CREATE UNIQUE INDEX idx_projections_unique ON projections (user_id, projection_type, key);

-- List all projections of a type for a user
CREATE INDEX idx_projections_user_type ON projections (user_id, projection_type);

-- RLS: users can only read their own projections (API reads via app_reader)
ALTER TABLE projections ENABLE ROW LEVEL SECURITY;

CREATE POLICY projections_user_isolation ON projections
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

-- Grants for existing roles
GRANT SELECT ON projections TO app_reader;
GRANT SELECT ON projections TO app_writer;

-- ────────────────────────────────────────────
-- app_worker role: BYPASSRLS for cross-user access
-- ────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_worker') THEN
        CREATE ROLE app_worker BYPASSRLS;
    END IF;
END
$$;

GRANT SELECT ON events TO app_worker;
GRANT SELECT, INSERT, UPDATE ON projections TO app_worker;
GRANT SELECT, INSERT, UPDATE ON background_jobs TO app_worker;
GRANT USAGE, SELECT ON SEQUENCE background_jobs_id_seq TO app_worker;

-- Grant app_worker to kura user (dev convenience)
GRANT app_worker TO kura;

-- ────────────────────────────────────────────
-- Trigger: auto-enqueue job on event INSERT
-- ────────────────────────────────────────────

CREATE OR REPLACE FUNCTION fn_enqueue_event_job()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO background_jobs (user_id, job_type, payload)
    VALUES (
        NEW.user_id,
        'projection.update',
        jsonb_build_object(
            'event_id', NEW.id,
            'event_type', NEW.event_type,
            'user_id', NEW.user_id
        )
    );
    PERFORM pg_notify('kura_jobs', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enqueue_event_job
    AFTER INSERT ON events
    FOR EACH ROW
    EXECUTE FUNCTION fn_enqueue_event_job();
