-- Adaptive abuse detection telemetry for CT3.4.
-- Tracks risk signals, shaping decisions, cooldown transitions, and UX impact.

CREATE TABLE IF NOT EXISTS security_abuse_telemetry (
    id                   BIGSERIAL PRIMARY KEY,
    timestamp            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id              UUID NOT NULL,
    path                 TEXT NOT NULL,
    method               TEXT NOT NULL,
    action               TEXT NOT NULL, -- allow | throttle | block | recovery
    risk_score           INTEGER NOT NULL,
    cooldown_active      BOOLEAN NOT NULL DEFAULT FALSE,
    cooldown_until       TIMESTAMPTZ,
    total_requests_60s   INTEGER NOT NULL,
    denied_requests_60s  INTEGER NOT NULL,
    unique_paths_60s     INTEGER NOT NULL,
    context_reads_60s    INTEGER NOT NULL,
    denied_ratio_60s     DOUBLE PRECISION NOT NULL,
    signals              TEXT[] NOT NULL DEFAULT '{}',
    false_positive_hint  BOOLEAN NOT NULL DEFAULT FALSE,
    ux_impact_hint       TEXT NOT NULL DEFAULT 'none', -- none | delayed | blocked
    response_status_code SMALLINT NOT NULL,
    response_time_ms     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_security_abuse_telemetry_ts
    ON security_abuse_telemetry (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_security_abuse_telemetry_user_ts
    ON security_abuse_telemetry (user_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_security_abuse_telemetry_action_ts
    ON security_abuse_telemetry (action, timestamp DESC);

GRANT INSERT ON security_abuse_telemetry TO app_writer;
GRANT SELECT ON security_abuse_telemetry TO app_reader;
GRANT USAGE, SELECT ON SEQUENCE security_abuse_telemetry_id_seq TO app_writer;
