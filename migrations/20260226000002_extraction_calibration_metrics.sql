-- Extraction calibration metrics and drift artifacts (2zc.5).
--
-- Tracks confidence calibration quality for evidence.claim.logged classes
-- and publishes weekly underperforming classes for learning loop follow-up.

CREATE TABLE IF NOT EXISTS extraction_calibration_metrics (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_granularity      TEXT NOT NULL CHECK (period_granularity IN ('day', 'week')),
    period_key              TEXT NOT NULL,
    claim_class             TEXT NOT NULL,
    parser_version          TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('healthy', 'monitor', 'degraded')),
    drift_status            TEXT NOT NULL CHECK (drift_status IN ('stable', 'drift_alert', 'insufficient_history')),
    drift_delta_brier       DOUBLE PRECISION,
    sample_count            INT NOT NULL CHECK (sample_count >= 0),
    correct_count           INT NOT NULL CHECK (correct_count >= 0),
    incorrect_count         INT NOT NULL CHECK (incorrect_count >= 0),
    avg_confidence          DOUBLE PRECISION NOT NULL CHECK (avg_confidence >= 0.0 AND avg_confidence <= 1.0),
    brier_score             DOUBLE PRECISION NOT NULL CHECK (brier_score >= 0.0),
    precision_high_conf     DOUBLE PRECISION,
    recall_high_conf        DOUBLE PRECISION,
    metric_data             JSONB NOT NULL DEFAULT '{}',
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (period_granularity, period_key, claim_class, parser_version)
);

CREATE INDEX IF NOT EXISTS idx_extraction_calibration_metrics_period
    ON extraction_calibration_metrics (period_granularity, period_key, status, brier_score DESC);

CREATE INDEX IF NOT EXISTS idx_extraction_calibration_metrics_claim
    ON extraction_calibration_metrics (claim_class, parser_version);


CREATE TABLE IF NOT EXISTS extraction_calibration_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status                  TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    window_days             INT NOT NULL CHECK (window_days > 0),
    total_claims            INT NOT NULL DEFAULT 0 CHECK (total_claims >= 0),
    considered_claims       INT NOT NULL DEFAULT 0 CHECK (considered_claims >= 0),
    metrics_written         INT NOT NULL DEFAULT 0 CHECK (metrics_written >= 0),
    underperforming_written INT NOT NULL DEFAULT 0 CHECK (underperforming_written >= 0),
    details                 JSONB NOT NULL DEFAULT '{}',
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extraction_calibration_runs_started
    ON extraction_calibration_runs (started_at DESC);


CREATE TABLE IF NOT EXISTS extraction_underperforming_classes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_key              TEXT NOT NULL,
    claim_class             TEXT NOT NULL,
    parser_version          TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('underperforming', 'drift_alert')),
    brier_score             DOUBLE PRECISION NOT NULL CHECK (brier_score >= 0.0),
    precision_high_conf     DOUBLE PRECISION,
    sample_count            INT NOT NULL CHECK (sample_count >= 0),
    details                 JSONB NOT NULL DEFAULT '{}',
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (period_key, claim_class, parser_version, status)
);

CREATE INDEX IF NOT EXISTS idx_extraction_underperforming_period
    ON extraction_underperforming_classes (period_key, status, brier_score DESC);

GRANT SELECT
ON extraction_calibration_metrics, extraction_calibration_runs, extraction_underperforming_classes
TO app_writer;

GRANT SELECT, INSERT, UPDATE, DELETE
ON extraction_calibration_metrics, extraction_calibration_runs, extraction_underperforming_classes
TO app_worker;
