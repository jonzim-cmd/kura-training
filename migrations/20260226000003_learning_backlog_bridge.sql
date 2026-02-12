-- Learning-to-backlog bridge artifacts (2zc.3).
--
-- Produces machine-readable candidate issue payloads from weekly learning
-- clusters and extraction calibration signals. Includes promotion checklist
-- metadata and duplicate/noise guardrail telemetry.

CREATE TABLE IF NOT EXISTS learning_backlog_candidates (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_key               TEXT NOT NULL UNIQUE,
    status                      TEXT NOT NULL DEFAULT 'candidate'
                                CHECK (status IN ('candidate', 'approved', 'dismissed', 'promoted')),
    source_type                 TEXT NOT NULL
                                CHECK (source_type IN ('issue_cluster', 'extraction_calibration')),
    source_period_key           TEXT NOT NULL,
    source_ref                  TEXT NOT NULL,
    priority_score              DOUBLE PRECISION NOT NULL
                                CHECK (priority_score >= 0.0 AND priority_score <= 1.0),
    title                       TEXT NOT NULL,
    root_cause_hypothesis       TEXT NOT NULL,
    impacted_metrics            JSONB NOT NULL DEFAULT '{}',
    suggested_updates           JSONB NOT NULL DEFAULT '{}',
    promotion_checklist         JSONB NOT NULL DEFAULT '{}',
    issue_payload               JSONB NOT NULL DEFAULT '{}',
    guardrails                  JSONB NOT NULL DEFAULT '{}',
    approval_required           BOOLEAN NOT NULL DEFAULT TRUE,
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_backlog_candidates_status_priority
    ON learning_backlog_candidates (status, priority_score DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_backlog_candidates_source
    ON learning_backlog_candidates (source_type, source_period_key, source_ref);


CREATE TABLE IF NOT EXISTS learning_backlog_bridge_runs (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status                      TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    source_period_key           TEXT,
    total_cluster_rows          INT NOT NULL DEFAULT 0 CHECK (total_cluster_rows >= 0),
    total_underperforming_rows  INT NOT NULL DEFAULT 0 CHECK (total_underperforming_rows >= 0),
    candidates_considered       INT NOT NULL DEFAULT 0 CHECK (candidates_considered >= 0),
    candidates_written          INT NOT NULL DEFAULT 0 CHECK (candidates_written >= 0),
    filtered_noise              INT NOT NULL DEFAULT 0 CHECK (filtered_noise >= 0),
    duplicates_skipped          INT NOT NULL DEFAULT 0 CHECK (duplicates_skipped >= 0),
    details                     JSONB NOT NULL DEFAULT '{}',
    started_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at                TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_learning_backlog_bridge_runs_started
    ON learning_backlog_bridge_runs (started_at DESC);

GRANT SELECT
ON learning_backlog_candidates, learning_backlog_bridge_runs
TO app_writer;

GRANT SELECT, INSERT, UPDATE, DELETE
ON learning_backlog_candidates, learning_backlog_bridge_runs
TO app_worker;
