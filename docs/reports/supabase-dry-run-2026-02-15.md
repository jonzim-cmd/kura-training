# Supabase Cutover Execution Report - 2026-02-15

## Summary

A full source-to-target migration was executed from VPS Postgres to Supabase, followed by production runtime switch to Supabase URLs for API and worker.

## Data Migration Result

- Source: VPS `docker-kura-postgres-1`
- Target: Supabase `postgres` (session pooler connection)
- Source snapshot isolation: `REPEATABLE READ, READ ONLY`
- Verification output:
  - `table_count = 45`
  - `mismatch_count = 0`
  - key counts:
    - `users = 1`
    - `events = 270`
    - `projections = 35`
    - `oauth_clients = 2`
    - `user_identities = 1`
    - `api_keys = 1`

## Migration State

- `_sqlx_migrations` source/target both had 32 migrations through `20260229000002`.

## Runtime Cutover Result

- API env switched to `KURA_API_DATABASE_URL` (Supabase session pooler)
- Worker env switched to `KURA_WORKER_DATABASE_URL` (Supabase session pooler)
- Services healthy after redeploy:
  - `docker-kura-api-1`
  - `docker-kura-worker-1`
  - `docker-kura-proxy-1`
- Gateway health check passed: `{ "status": "ok", "version": "0.1.0" }`

## Auth Compatibility Regression (Strategie B)

- Test target: `cargo test -p kura-api routes::auth::tests -- --nocapture`
- Database target: Supabase session pooler (`aws-1-eu-west-1.pooler.supabase.com:5432/postgres`)
- Result: `15 passed; 0 failed`
- Covered paths:
  - email/password login credential validation
  - OAuth authorization code exchange (PKCE fail + success)
  - refresh token rotation + replay rejection
  - device token consume-once behavior
- Data hygiene:
  - temporary test rows used random `*-client-*` and `*-user-*` prefixes
  - cleanup executed after run
  - post-cleanup check: `users_total = 1`, test-prefixed users/clients = `0`
- Detailed evidence: `docs/reports/supabase-auth-compatibility-2026-02-15.md`

## Incident During Cutover

- Worker crash loop occurred with `permission denied to set role "app_worker"`.
- Root cause: Supabase role membership for `postgres -> app_worker` had `set_option=false`.
- Fix: `GRANT app_worker TO postgres WITH SET TRUE;` (and same for other app roles).
- Outcome: worker stable and healthy.

## Gate Status

- Downtime <= 15 min: NOT formally measured yet
- Rollback <= 30 min: NOT drilled yet
- Data integrity parity: PASS for migration checks performed
- Runtime health (API/worker): PASS

## Remaining Work

1. Run and record formal timed rollback probe.
2. Complete baseline guardrail metadata (PITR/spend/restore drill owner+date).
