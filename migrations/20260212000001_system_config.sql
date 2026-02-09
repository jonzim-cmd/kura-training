-- System Config: deployment-static configuration (no per-user data)
-- Stores system-level data (dimensions, event conventions, interview guide)
-- that is identical for all users and changes only on code deployment.
--
-- No user_id, no RLS — this is public system knowledge.

CREATE TABLE IF NOT EXISTS system_config (
    key         TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}',
    version     BIGINT NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Worker writes on startup
GRANT SELECT, INSERT, UPDATE ON system_config TO app_worker;

-- API reads for the agent
GRANT SELECT ON system_config TO app_reader;
GRANT SELECT ON system_config TO app_writer;
