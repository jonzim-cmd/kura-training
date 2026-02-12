-- Security profile rollout controls and guardrail decision records (CT3.7).

ALTER TABLE security_abuse_telemetry
    ADD COLUMN IF NOT EXISTS profile TEXT NOT NULL DEFAULT 'adaptive';

CREATE TABLE IF NOT EXISTS security_profile_rollout (
    id                        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
    default_profile           TEXT NOT NULL DEFAULT 'default'
        CHECK (default_profile IN ('default', 'adaptive', 'strict')),
    adaptive_rollout_percent  SMALLINT NOT NULL DEFAULT 0
        CHECK (adaptive_rollout_percent >= 0 AND adaptive_rollout_percent <= 100),
    strict_rollout_percent    SMALLINT NOT NULL DEFAULT 0
        CHECK (strict_rollout_percent >= 0 AND strict_rollout_percent <= 100),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by                UUID,
    notes                     TEXT,
    CHECK (adaptive_rollout_percent + strict_rollout_percent <= 100)
);

INSERT INTO security_profile_rollout (id)
VALUES (TRUE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS security_profile_user_overrides (
    user_id      UUID PRIMARY KEY,
    profile      TEXT NOT NULL CHECK (profile IN ('default', 'adaptive', 'strict')),
    reason       TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by   UUID NOT NULL
);

CREATE TABLE IF NOT EXISTS security_profile_rollout_decisions (
    id               BIGSERIAL PRIMARY KEY,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_user_id    UUID NOT NULL,
    decision         TEXT NOT NULL,
    rationale        TEXT NOT NULL,
    metrics_snapshot JSONB NOT NULL DEFAULT '{}',
    rollout_config   JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_security_profile_rollout_decisions_ts
    ON security_profile_rollout_decisions (timestamp DESC);

GRANT SELECT, INSERT, UPDATE ON security_profile_rollout TO app_writer;
GRANT SELECT ON security_profile_rollout TO app_reader;

GRANT SELECT, INSERT, UPDATE, DELETE ON security_profile_user_overrides TO app_writer;
GRANT SELECT ON security_profile_user_overrides TO app_reader;

GRANT SELECT, INSERT ON security_profile_rollout_decisions TO app_writer;
GRANT SELECT ON security_profile_rollout_decisions TO app_reader;
GRANT USAGE, SELECT ON SEQUENCE security_profile_rollout_decisions_id_seq TO app_writer;
