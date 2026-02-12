-- Unknown-dimension mining artifacts (2zc.6).
--
-- Mines recurring unknown/provisional observation patterns and produces
-- ranked contract proposals with evidence bundles. Accepted proposals are
-- routed into the learning backlog bridge.

CREATE TABLE IF NOT EXISTS unknown_dimension_proposals (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_key        TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL DEFAULT 'candidate'
                        CHECK (status IN ('candidate', 'accepted', 'dismissed', 'promoted')),
    period_key          TEXT NOT NULL,
    cluster_signature   TEXT NOT NULL,
    dimension_seed      TEXT NOT NULL,
    proposal_score      DOUBLE PRECISION NOT NULL
                        CHECK (proposal_score >= 0.0 AND proposal_score <= 1.0),
    confidence          DOUBLE PRECISION NOT NULL
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    event_count         INT NOT NULL CHECK (event_count >= 0),
    unique_users        INT NOT NULL CHECK (unique_users >= 0),
    suggested_dimension JSONB NOT NULL DEFAULT '{}',
    evidence_bundle     JSONB NOT NULL DEFAULT '{}',
    risk_notes          JSONB NOT NULL DEFAULT '[]',
    proposal_payload    JSONB NOT NULL DEFAULT '{}',
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_unknown_dimension_proposals_status_score
    ON unknown_dimension_proposals (status, proposal_score DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_unknown_dimension_proposals_period
    ON unknown_dimension_proposals (period_key, cluster_signature);


CREATE TABLE IF NOT EXISTS unknown_dimension_mining_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status                  TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    window_days             INT NOT NULL CHECK (window_days > 0),
    total_observations      INT NOT NULL DEFAULT 0 CHECK (total_observations >= 0),
    considered_observations INT NOT NULL DEFAULT 0 CHECK (considered_observations >= 0),
    proposals_written       INT NOT NULL DEFAULT 0 CHECK (proposals_written >= 0),
    filtered_invalid_rows   INT NOT NULL DEFAULT 0 CHECK (filtered_invalid_rows >= 0),
    filtered_noise          INT NOT NULL DEFAULT 0 CHECK (filtered_noise >= 0),
    details                 JSONB NOT NULL DEFAULT '{}',
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_unknown_dimension_mining_runs_started
    ON unknown_dimension_mining_runs (started_at DESC);

GRANT SELECT
ON unknown_dimension_proposals, unknown_dimension_mining_runs
TO app_writer;

GRANT SELECT, INSERT, UPDATE, DELETE
ON unknown_dimension_proposals, unknown_dimension_mining_runs
TO app_worker;


DO $$
BEGIN
    IF to_regclass('learning_backlog_candidates') IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'learning_backlog_candidates_source_type_check'
              AND conrelid = 'learning_backlog_candidates'::regclass
        ) THEN
            ALTER TABLE learning_backlog_candidates
                DROP CONSTRAINT learning_backlog_candidates_source_type_check;
        END IF;

        ALTER TABLE learning_backlog_candidates
            ADD CONSTRAINT learning_backlog_candidates_source_type_check
            CHECK (
                source_type IN (
                    'issue_cluster',
                    'extraction_calibration',
                    'unknown_dimension'
                )
            );
    END IF;
END $$;
