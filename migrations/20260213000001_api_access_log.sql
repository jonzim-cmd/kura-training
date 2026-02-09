-- Access pattern logging: structured API access data for analytics.
-- No RLS — infrastructure data, not user data.

CREATE TABLE api_access_log (
    id               BIGSERIAL PRIMARY KEY,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id          UUID,
    method           TEXT NOT NULL,
    path             TEXT NOT NULL,
    projection_type  TEXT,
    key              TEXT,
    status_code      SMALLINT NOT NULL,
    batch_size       SMALLINT,
    response_time_ms INTEGER NOT NULL
);

-- Analytics indexes
CREATE INDEX idx_access_log_ts ON api_access_log (timestamp DESC);
CREATE INDEX idx_access_log_user_ts ON api_access_log (user_id, timestamp DESC);
CREATE INDEX idx_access_log_projection ON api_access_log (projection_type, timestamp DESC)
    WHERE projection_type IS NOT NULL;

-- Grants
GRANT INSERT ON api_access_log TO app_writer;
GRANT USAGE, SELECT ON SEQUENCE api_access_log_id_seq TO app_writer;
GRANT SELECT ON api_access_log TO app_reader;
