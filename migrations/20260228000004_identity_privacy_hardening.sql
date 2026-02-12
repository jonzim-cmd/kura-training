-- Privacy/Auth hardening:
-- - keep break-glass audit append-only across user deletions
-- - prevent user deletion blockers from oauth_device_codes
-- - tighten identity table invariants
-- - refresh delete_user_account() to cover new auth tables

-- support_access_audit must remain append-only even when users are deleted.
-- Drop FK cascade so historical audit rows survive account deletion.
ALTER TABLE support_access_audit
    DROP CONSTRAINT IF EXISTS support_access_audit_target_user_id_fkey;

-- Ensure device code approvals never block account deletion.
ALTER TABLE oauth_device_codes
    DROP CONSTRAINT IF EXISTS oauth_device_codes_approved_user_id_fkey;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'oauth_device_codes_approved_user_id_fkey'
    ) THEN
        ALTER TABLE oauth_device_codes
            ADD CONSTRAINT oauth_device_codes_approved_user_id_fkey
            FOREIGN KEY (approved_user_id)
            REFERENCES users(id)
            ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'user_identities_provider_subject_not_blank'
    ) THEN
        ALTER TABLE user_identities
            ADD CONSTRAINT user_identities_provider_subject_not_blank
            CHECK (length(trim(provider_subject)) > 0);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'user_identities_email_password_requires_email'
    ) THEN
        ALTER TABLE user_identities
            ADD CONSTRAINT user_identities_email_password_requires_email
            CHECK (provider <> 'email_password' OR email_norm IS NOT NULL);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_identities_email_password_user
    ON user_identities (user_id)
    WHERE provider = 'email_password';

-- Refresh account deletion function for new auth/privacy tables.
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

    -- 6. Delete events (requires SECURITY DEFINER)
    DELETE FROM events WHERE user_id = p_user_id;
    GET DIAGNOSTICS n_events = ROW_COUNT;

    -- 7. Delete user account (cascades user_identities + analysis_subjects)
    DELETE FROM users WHERE id = p_user_id;

    RETURN QUERY SELECT n_events, n_projections;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Keep execute grant and ensure function can delete device-code rows.
GRANT EXECUTE ON FUNCTION delete_user_account(UUID) TO app_writer;
GRANT DELETE ON oauth_device_codes TO app_writer;
