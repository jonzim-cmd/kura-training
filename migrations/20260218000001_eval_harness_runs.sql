-- Offline eval harness run/version storage.
-- Stores one run row plus per-projection artifacts.

CREATE TABLE IF NOT EXISTS inference_eval_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source              TEXT NOT NULL
                        CHECK (source IN ('projection_history', 'event_store', 'combined')),
    projection_types    JSONB NOT NULL DEFAULT '[]',
    strength_engine     TEXT NOT NULL DEFAULT 'closed_form',
    status              TEXT NOT NULL
                        CHECK (status IN ('completed', 'failed')),
    config              JSONB NOT NULL DEFAULT '{}',
    summary             JSONB NOT NULL DEFAULT '{}',
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inference_eval_runs_user_created
    ON inference_eval_runs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_inference_eval_runs_source_created
    ON inference_eval_runs (source, created_at DESC);

CREATE TABLE IF NOT EXISTS inference_eval_artifacts (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES inference_eval_runs(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source              TEXT NOT NULL
                        CHECK (source IN ('projection_history', 'event_store')),
    projection_type     TEXT NOT NULL
                        CHECK (projection_type IN (
                            'semantic_memory',
                            'strength_inference',
                            'readiness_inference'
                        )),
    projection_key      TEXT NOT NULL,
    artifact            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inference_eval_artifacts_run
    ON inference_eval_artifacts (run_id, id);

CREATE INDEX IF NOT EXISTS idx_inference_eval_artifacts_user_type_key
    ON inference_eval_artifacts (user_id, projection_type, projection_key, created_at DESC);

ALTER TABLE inference_eval_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE inference_eval_artifacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY inference_eval_runs_user_isolation ON inference_eval_runs
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY inference_eval_runs_user_insert ON inference_eval_runs
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY inference_eval_artifacts_user_isolation ON inference_eval_artifacts
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY inference_eval_artifacts_user_insert ON inference_eval_artifacts
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

GRANT SELECT ON inference_eval_runs, inference_eval_artifacts TO app_reader;
GRANT SELECT, INSERT ON inference_eval_runs, inference_eval_artifacts TO app_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON inference_eval_runs, inference_eval_artifacts TO app_worker;
GRANT USAGE, SELECT ON SEQUENCE inference_eval_artifacts_id_seq TO app_worker;
