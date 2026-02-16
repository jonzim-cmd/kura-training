-- Supabase linter hardening pass:
-- 1) Enable RLS on public tables that still have relrowsecurity = false
-- 2) Add an internal-only access policy for existing app roles on those tables
-- 3) Pin search_path for mutable functions
-- 4) Add covering indexes for reported unindexed foreign keys

DO $$
DECLARE
    rec RECORD;
    internal_roles TEXT;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ' ORDER BY rolname)
    INTO internal_roles
    FROM pg_roles
    WHERE rolname IN ('app_reader', 'app_writer', 'app_worker', 'app_migrator');

    IF internal_roles IS NULL THEN
        RAISE EXCEPTION 'Required app roles are missing; cannot create internal_access policies';
    END IF;

    FOR rec IN
        SELECT n.nspname AS schema_name, c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relrowsecurity = FALSE
          AND c.relispartition = FALSE
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
            rec.schema_name,
            rec.table_name
        );

        IF NOT EXISTS (
            SELECT 1
            FROM pg_policies p
            WHERE p.schemaname = rec.schema_name
              AND p.tablename = rec.table_name
              AND p.policyname = 'internal_access'
        ) THEN
            EXECUTE format(
                'CREATE POLICY internal_access ON %I.%I FOR ALL TO %s USING (true) WITH CHECK (true)',
                rec.schema_name,
                rec.table_name,
                internal_roles
            );
        END IF;
    END LOOP;
END
$$;

-- Supabase linter: function_search_path_mutable
ALTER FUNCTION IF EXISTS public.fn_enqueue_event_job() SET search_path = public, pg_temp;
ALTER FUNCTION IF EXISTS public.delete_user_account(UUID) SET search_path = public, pg_temp;

-- Supabase linter: unindexed_foreign_keys
CREATE INDEX IF NOT EXISTS idx_access_requests_invite_token_id ON access_requests (invite_token_id);
CREATE INDEX IF NOT EXISTS idx_access_requests_reviewed_by ON access_requests (reviewed_by);
CREATE INDEX IF NOT EXISTS idx_inference_scheduler_state_in_flight_job_id ON inference_scheduler_state (in_flight_job_id);
CREATE INDEX IF NOT EXISTS idx_invite_tokens_created_by ON invite_tokens (created_by);
CREATE INDEX IF NOT EXISTS idx_invite_tokens_used_by ON invite_tokens (used_by);
CREATE INDEX IF NOT EXISTS idx_oauth_authorization_codes_user_id ON oauth_authorization_codes (user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_tokens_access_token_id ON oauth_refresh_tokens (access_token_id);
CREATE INDEX IF NOT EXISTS idx_projections_last_event_id ON projections (last_event_id);
CREATE INDEX IF NOT EXISTS idx_users_invited_by_token ON users (invited_by_token);
