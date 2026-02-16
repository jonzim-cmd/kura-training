-- Password reset flow + account deletion hardening.
-- - Adds single-use password reset tokens (hashed-at-rest).
-- - Converts self-service deletion to soft-delete metadata (30-day grace).
-- - Allows worker role to execute final hard-delete function.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deletion_scheduled_for TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_deletion_window_consistent'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_deletion_window_consistent
            CHECK (
                (deletion_requested_at IS NULL AND deletion_scheduled_for IS NULL)
                OR (
                    deletion_requested_at IS NOT NULL
                    AND deletion_scheduled_for IS NOT NULL
                    AND deletion_scheduled_for >= deletion_requested_at
                )
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_deletion_scheduled_for
    ON users (deletion_scheduled_for)
    WHERE is_active = FALSE AND deletion_scheduled_for IS NOT NULL;

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_created
    ON password_reset_tokens (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_active
    ON password_reset_tokens (expires_at)
    WHERE used_at IS NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON password_reset_tokens TO app_writer;
GRANT SELECT ON password_reset_tokens TO app_reader;
GRANT SELECT ON password_reset_tokens TO app_worker;

ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    internal_roles TEXT;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ' ORDER BY rolname)
    INTO internal_roles
    FROM pg_roles
    WHERE rolname IN ('app_reader', 'app_writer', 'app_worker', 'app_migrator', 'service_role');

    IF internal_roles IS NULL THEN
        RAISE EXCEPTION 'Required app roles are missing; cannot create internal_access policy';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'password_reset_tokens'
          AND policyname = 'internal_access'
    ) THEN
        EXECUTE format(
            'CREATE POLICY internal_access ON public.password_reset_tokens FOR ALL TO %s USING (true) WITH CHECK (true)',
            internal_roles
        );
    END IF;
END $$;

GRANT EXECUTE ON FUNCTION delete_user_account(UUID) TO app_worker;
