-- Security kill-switch state + audit trail for incident response (CT3.5).

CREATE TABLE IF NOT EXISTS security_kill_switch_state (
    id           BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
    is_active    BOOLEAN NOT NULL DEFAULT FALSE,
    reason       TEXT,
    activated_at TIMESTAMPTZ,
    activated_by UUID,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO security_kill_switch_state (id, is_active)
VALUES (TRUE, FALSE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS security_kill_switch_audit (
    id            BIGSERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action        TEXT NOT NULL, -- activated | deactivated | blocked_request
    actor_user_id UUID,
    target_user_id UUID,
    path          TEXT,
    method        TEXT,
    reason        TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_security_kill_switch_audit_ts
    ON security_kill_switch_audit (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_security_kill_switch_audit_action_ts
    ON security_kill_switch_audit (action, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_security_kill_switch_audit_target_ts
    ON security_kill_switch_audit (target_user_id, timestamp DESC)
    WHERE target_user_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE ON security_kill_switch_state TO app_writer;
GRANT SELECT ON security_kill_switch_state TO app_reader;
GRANT SELECT, INSERT ON security_kill_switch_audit TO app_writer;
GRANT SELECT ON security_kill_switch_audit TO app_reader;
GRANT USAGE, SELECT ON SEQUENCE security_kill_switch_audit_id_seq TO app_writer;
