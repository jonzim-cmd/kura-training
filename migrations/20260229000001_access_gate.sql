-- Access Gate System: invite_tokens, access_requests, user consent
-- Supports SIGNUP_GATE modes: invite (launch), open (growth), payment (future)

-- Invite tokens — generated on approval or manually by admin
CREATE TABLE invite_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token           TEXT NOT NULL UNIQUE,
    email           TEXT,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    used_by         UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_invite_tokens_token ON invite_tokens (token) WHERE used_at IS NULL;
CREATE INDEX idx_invite_tokens_email ON invite_tokens (email) WHERE used_at IS NULL;

-- Access requests — public waitlist submissions
CREATE TABLE access_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL,
    name            TEXT,
    context         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    invite_token_id UUID REFERENCES invite_tokens(id) ON DELETE SET NULL
);

CREATE INDEX idx_access_requests_email_status ON access_requests (email, status);
CREATE INDEX idx_access_requests_status ON access_requests (status) WHERE status = 'pending';

-- Add CHECK constraint for valid status values
ALTER TABLE access_requests
    ADD CONSTRAINT access_requests_status_check
    CHECK (status IN ('pending', 'approved', 'rejected'));

-- Extend users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_anonymized_learning BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_by_token UUID REFERENCES invite_tokens(id) ON DELETE SET NULL;

-- Permissions: app_writer needs full access for API operations
GRANT SELECT, INSERT, UPDATE ON invite_tokens TO app_writer;
GRANT SELECT, INSERT, UPDATE ON access_requests TO app_writer;
GRANT SELECT ON invite_tokens, access_requests TO app_reader;

-- Update account deletion function to clean up invite references
CREATE OR REPLACE FUNCTION delete_user_account(p_user_id UUID)
RETURNS TABLE(events_deleted BIGINT, projections_deleted BIGINT) AS $$
DECLARE
    n_events BIGINT;
    n_projections BIGINT;
BEGIN
    -- 1. Delete background jobs (no FK to events, safe to go first)
    DELETE FROM background_jobs WHERE user_id = p_user_id;

    -- 2. Delete projections (FK: last_event_id -> events)
    DELETE FROM projections WHERE user_id = p_user_id;
    GET DIAGNOSTICS n_projections = ROW_COUNT;

    -- 3. Delete audit-style operational logs containing user_id
    DELETE FROM audit_log WHERE user_id = p_user_id;
    DELETE FROM api_access_log WHERE user_id = p_user_id;

    -- 4. Delete oauth/device grants and tokens
    DELETE FROM oauth_device_codes WHERE approved_user_id = p_user_id;
    DELETE FROM oauth_refresh_tokens WHERE user_id = p_user_id;
    DELETE FROM oauth_access_tokens WHERE user_id = p_user_id;
    DELETE FROM oauth_authorization_codes WHERE user_id = p_user_id;

    -- 5. Delete API keys
    DELETE FROM api_keys WHERE user_id = p_user_id;

    -- 6. Nullify invite token references (keep tokens for audit trail)
    UPDATE invite_tokens SET used_by = NULL WHERE used_by = p_user_id;
    UPDATE invite_tokens SET created_by = NULL WHERE created_by = p_user_id;
    UPDATE access_requests SET reviewed_by = NULL WHERE reviewed_by = p_user_id;

    -- 7. Delete events (requires SECURITY DEFINER)
    DELETE FROM events WHERE user_id = p_user_id;
    GET DIAGNOSTICS n_events = ROW_COUNT;

    -- 8. Delete user account (cascades user_identities + analysis_subjects)
    DELETE FROM users WHERE id = p_user_id;

    RETURN QUERY SELECT n_events, n_projections;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

GRANT EXECUTE ON FUNCTION delete_user_account(UUID) TO app_writer;
