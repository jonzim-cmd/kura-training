-- Cross-user learning telemetry clustering artifacts (2zc.2).
--
-- Stores explainable issue clusters derived from learning.signal.logged events.
-- No raw user identifiers are persisted; only pseudonymous aggregate counts and
-- representative telemetry snippets.

CREATE TABLE IF NOT EXISTS learning_issue_clusters (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_granularity  TEXT NOT NULL CHECK (period_granularity IN ('day', 'week')),
    period_key          TEXT NOT NULL,
    cluster_signature   TEXT NOT NULL,
    score               DOUBLE PRECISION NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
    event_count         INT NOT NULL CHECK (event_count >= 0),
    unique_users        INT NOT NULL CHECK (unique_users >= 0),
    cluster_data        JSONB NOT NULL DEFAULT '{}',
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (period_granularity, period_key, cluster_signature)
);

CREATE INDEX IF NOT EXISTS idx_learning_issue_clusters_period_score
    ON learning_issue_clusters (period_granularity, period_key, score DESC);

CREATE INDEX IF NOT EXISTS idx_learning_issue_clusters_signature
    ON learning_issue_clusters (cluster_signature);


CREATE TABLE IF NOT EXISTS learning_issue_cluster_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status                  TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    window_days             INT NOT NULL CHECK (window_days > 0),
    total_signals           INT NOT NULL DEFAULT 0 CHECK (total_signals >= 0),
    considered_signals      INT NOT NULL DEFAULT 0 CHECK (considered_signals >= 0),
    clusters_written        INT NOT NULL DEFAULT 0 CHECK (clusters_written >= 0),
    filtered_low_confidence INT NOT NULL DEFAULT 0 CHECK (filtered_low_confidence >= 0),
    filtered_min_support    INT NOT NULL DEFAULT 0 CHECK (filtered_min_support >= 0),
    filtered_unique_users   INT NOT NULL DEFAULT 0 CHECK (filtered_unique_users >= 0),
    details                 JSONB NOT NULL DEFAULT '{}',
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_learning_issue_cluster_runs_started
    ON learning_issue_cluster_runs (started_at DESC);

GRANT SELECT ON learning_issue_clusters, learning_issue_cluster_runs TO app_writer;
GRANT SELECT, INSERT, UPDATE, DELETE
ON learning_issue_clusters, learning_issue_cluster_runs TO app_worker;
