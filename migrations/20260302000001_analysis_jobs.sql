-- Async deep-analysis jobs for external agent orchestration.
-- Backend-only contract: create job -> worker computes -> poll status/result.

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    objective       TEXT NOT NULL,
    horizon_days    INT NOT NULL DEFAULT 90
                    CHECK (horizon_days >= 1 AND horizon_days <= 3650),
    focus           JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(focus) = 'array'),
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_payload  JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code      TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_user_status_created
    ON analysis_jobs (user_id, status, created_at DESC);

ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'analysis_jobs'
          AND policyname = 'analysis_jobs_user_isolation'
    ) THEN
        CREATE POLICY analysis_jobs_user_isolation ON analysis_jobs
            USING (user_id = current_setting('kura.current_user_id', true)::UUID);
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'analysis_jobs'
          AND policyname = 'analysis_jobs_user_insert'
    ) THEN
        CREATE POLICY analysis_jobs_user_insert ON analysis_jobs
            FOR INSERT
            WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);
    END IF;
END
$$;

GRANT SELECT ON analysis_jobs TO app_reader;
GRANT SELECT, INSERT ON analysis_jobs TO app_writer;
GRANT SELECT, INSERT, UPDATE ON analysis_jobs TO app_worker;
