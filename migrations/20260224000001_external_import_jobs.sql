-- External import async job tracking (tm5.6)
-- File import flow: upload payload -> queue background job -> parse/map/validate/write
-- -> persist structured receipt for status polling and auditability.

CREATE TABLE IF NOT EXISTS external_import_jobs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id),
    provider                TEXT NOT NULL,
    provider_user_id        TEXT NOT NULL,
    file_format             TEXT NOT NULL
                            CHECK (file_format IN ('fit', 'tcx', 'gpx')),
    ingestion_method        TEXT NOT NULL DEFAULT 'file_import'
                            CHECK (ingestion_method IN (
                                'file_import',
                                'connector_api',
                                'manual_backfill'
                            )),
    external_activity_id    TEXT NOT NULL,
    external_event_version  TEXT,
    raw_payload_ref         TEXT,
    payload_text            TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    source_identity_key     TEXT,
    payload_fingerprint     TEXT,
    idempotency_key         TEXT,
    receipt                 JSONB NOT NULL DEFAULT '{}',
    error_code              TEXT,
    error_message           TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ
);

CREATE INDEX idx_external_import_jobs_user_status
    ON external_import_jobs (user_id, status, created_at DESC);

CREATE INDEX idx_external_import_jobs_source
    ON external_import_jobs (
        user_id,
        provider,
        provider_user_id,
        external_activity_id,
        created_at DESC
    );

ALTER TABLE external_import_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY external_import_jobs_user_isolation ON external_import_jobs
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY external_import_jobs_user_insert ON external_import_jobs
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

GRANT SELECT ON external_import_jobs TO app_reader;
GRANT SELECT, INSERT ON external_import_jobs TO app_writer;
GRANT SELECT, INSERT, UPDATE ON external_import_jobs TO app_worker;
