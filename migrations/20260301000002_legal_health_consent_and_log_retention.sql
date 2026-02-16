-- Legal hardening:
-- 1) Explicit Art. 9 GDPR consent fields for health-related processing.
-- 2) Auditable log-retention runs + worker privileges for scheduled cleanup.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS consent_health_data_processing BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS consent_health_data_processing_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS consent_health_data_processing_version TEXT,
    ADD COLUMN IF NOT EXISTS consent_health_data_withdrawn_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_health_consent_enabled_requires_metadata'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_health_consent_enabled_requires_metadata
            CHECK (
                consent_health_data_processing = FALSE
                OR (
                    consent_health_data_processing_at IS NOT NULL
                    AND consent_health_data_processing_version IS NOT NULL
                    AND consent_health_data_withdrawn_at IS NULL
                )
            );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_health_consent_withdrawn_requires_prior_consent'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_health_consent_withdrawn_requires_prior_consent
            CHECK (
                consent_health_data_withdrawn_at IS NULL
                OR (
                    consent_health_data_processing = FALSE
                    AND consent_health_data_processing_at IS NOT NULL
                    AND consent_health_data_withdrawn_at >= consent_health_data_processing_at
                )
            );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS log_retention_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT NOT NULL DEFAULT 'completed'
                CHECK (status IN ('completed', 'failed')),
    details     JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_log_retention_runs_started_at
    ON log_retention_runs (started_at DESC);

GRANT SELECT ON log_retention_runs TO app_reader;
GRANT SELECT ON log_retention_runs TO app_writer;
GRANT SELECT, INSERT, UPDATE ON log_retention_runs TO app_worker;
GRANT USAGE, SELECT ON SEQUENCE log_retention_runs_id_seq TO app_worker;

-- Retention cleanup runs in app_worker context.
GRANT DELETE ON api_access_log TO app_worker;
GRANT DELETE ON security_abuse_telemetry TO app_worker;
GRANT DELETE ON security_kill_switch_audit TO app_worker;
GRANT DELETE ON support_access_audit TO app_worker;
GRANT DELETE ON password_reset_tokens TO app_worker;
