-- Account deletion: DSGVO Art. 17 (Right to Erasure)
-- Training/health data is special category (Art. 9) — hard delete required.
--
-- Strategy: SECURITY DEFINER function bypasses RLS and the REVOKE DELETE
-- on events. Only callable via app_writer role (API server).

-- Add is_admin flag to users table
ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Hard-delete all data for a given user.
-- Returns the count of deleted events (for logging/confirmation).
CREATE OR REPLACE FUNCTION delete_user_account(p_user_id UUID)
RETURNS TABLE(events_deleted BIGINT, projections_deleted BIGINT) AS $$
DECLARE
    n_events BIGINT;
    n_projections BIGINT;
BEGIN
    -- 1. Delete background jobs (no FK to events, safe to go first)
    DELETE FROM background_jobs WHERE user_id = p_user_id;

    -- 2. Delete projections (FK: last_event_id → events)
    DELETE FROM projections WHERE user_id = p_user_id;
    GET DIAGNOSTICS n_projections = ROW_COUNT;

    -- 3. Delete audit_log entries (no FK, but contains user data)
    DELETE FROM audit_log WHERE user_id = p_user_id;

    -- 4. Delete access log entries
    DELETE FROM api_access_log WHERE user_id = p_user_id;

    -- 5. Delete OAuth tokens (FK chain: refresh → access → user)
    DELETE FROM oauth_refresh_tokens WHERE user_id = p_user_id;
    DELETE FROM oauth_access_tokens WHERE user_id = p_user_id;
    DELETE FROM oauth_authorization_codes WHERE user_id = p_user_id;

    -- 6. Delete API keys
    DELETE FROM api_keys WHERE user_id = p_user_id;

    -- 7. Delete events (this is the one that needs SECURITY DEFINER)
    DELETE FROM events WHERE user_id = p_user_id;
    GET DIAGNOSTICS n_events = ROW_COUNT;

    -- 8. Delete user account
    DELETE FROM users WHERE id = p_user_id;

    RETURN QUERY SELECT n_events, n_projections;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Only app_writer (API server) can call this function
REVOKE ALL ON FUNCTION delete_user_account(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION delete_user_account(UUID) TO app_writer;

-- app_writer needs DELETE on auth tables for the function to work
-- (SECURITY DEFINER runs as function owner, but explicit grants are cleaner)
GRANT DELETE ON oauth_refresh_tokens TO app_writer;
GRANT DELETE ON oauth_access_tokens TO app_writer;
GRANT DELETE ON oauth_authorization_codes TO app_writer;
GRANT DELETE ON api_keys TO app_writer;
GRANT DELETE ON users TO app_writer;
GRANT DELETE ON background_jobs TO app_writer;
GRANT DELETE ON audit_log TO app_writer;
GRANT DELETE ON api_access_log TO app_writer;
