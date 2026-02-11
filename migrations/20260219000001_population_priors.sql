-- Privacy-safe population priors for Bayesian inference blending.
--
-- Stores only aggregated cohort artifacts. No per-user identifiers are
-- persisted in these tables.

CREATE TABLE IF NOT EXISTS population_prior_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    projection_type     TEXT NOT NULL CHECK (projection_type IN ('strength_inference', 'readiness_inference')),
    target_key          TEXT NOT NULL,
    cohort_key          TEXT NOT NULL,
    prior_payload       JSONB NOT NULL DEFAULT '{}',
    participants_count  INT NOT NULL CHECK (participants_count > 0),
    sample_size         INT NOT NULL CHECK (sample_size > 0),
    min_cohort_size     INT NOT NULL CHECK (min_cohort_size > 0),
    source_window_days  INT NOT NULL CHECK (source_window_days > 0),
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (projection_type, target_key, cohort_key)
);

CREATE INDEX IF NOT EXISTS idx_population_prior_profiles_lookup
    ON population_prior_profiles (projection_type, target_key, cohort_key);

CREATE INDEX IF NOT EXISTS idx_population_prior_profiles_computed
    ON population_prior_profiles (computed_at DESC);


CREATE TABLE IF NOT EXISTS population_prior_refresh_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status              TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    users_opted_in      INT NOT NULL DEFAULT 0 CHECK (users_opted_in >= 0),
    cohorts_considered  INT NOT NULL DEFAULT 0 CHECK (cohorts_considered >= 0),
    priors_written      INT NOT NULL DEFAULT 0 CHECK (priors_written >= 0),
    details             JSONB NOT NULL DEFAULT '{}',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_population_prior_refresh_runs_started
    ON population_prior_refresh_runs (started_at DESC);


-- Access policy:
-- - app_worker can fully manage artifacts
-- - app_writer can inspect artifacts for internal tooling
-- - no direct app_reader grant to keep cross-user aggregates internal
GRANT SELECT ON population_prior_profiles, population_prior_refresh_runs TO app_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON population_prior_profiles, population_prior_refresh_runs TO app_worker;

