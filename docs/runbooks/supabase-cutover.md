# Supabase Cutover Runbook (Strategie B: DB-only)

## 1. Scope

This runbook covers the launch-critical DB cutover from VPS Postgres to Supabase Pro.
Auth strategy is fixed to `AUTH_STRATEGY=B`: auth logic remains in Kura API (`users`, `api_keys`, `oauth_*` stay source of truth).

## 2. Environment Baseline

- Supabase project ref: `slawzzhovquintrsmfby`
- Region: EU
- Primary DB name: `postgres`
- Direct endpoint (5432): `db.slawzzhovquintrsmfby.supabase.co`
- Session pooler endpoint (5432): `aws-1-eu-west-1.pooler.supabase.com`

## 3. Connection Policy (Current Production)

- API runtime: session pooler
- Worker runtime: session pooler for query path, optional dedicated LISTEN URL via `KURA_WORKER_LISTEN_DATABASE_URL`
- Migration/admin operations: session pooler (current), direct endpoint planned when network path supports it

Note: direct endpoint was not reachable from current VPS network path at cutover time (IPv6 routing limitation), so production uses session pooler.

## 4. Required Secrets (names only)

- `KURA_API_DATABASE_URL`
- `KURA_WORKER_DATABASE_URL`
- `KURA_WORKER_LISTEN_DATABASE_URL` (optional, for direct LISTEN/NOTIFY path)
- `KURA_AGENT_MODEL_ATTESTATION_SECRET`
- `KURA_API_KEY`
- `KURA_DB_PASSWORD` (legacy local fallback path; still present in compose)

Production guardrail: `docker/compose.production.yml` requires `KURA_API_DATABASE_URL` and `KURA_WORKER_DATABASE_URL` explicitly (no local DB fallback for API/worker).

## 5. Preflight Checklist

1. Confirm Supabase project reachable from VPS.
2. Confirm migration role setup exists (`app_reader`, `app_writer`, `app_worker`, `app_migrator`, `kura`).
3. Confirm `SET ROLE app_worker` works for runtime login role (`postgres`).
4. Run schema drift gate:
   - `scripts/check-migration-drift.sh --database-url "$KURA_API_DATABASE_URL" --migrations-dir migrations`
5. Confirm all services healthy before freeze window.
6. Confirm source row counts snapshot for key tables (`users`, `events`, `projections`, auth tables).

## 6. Cutover Procedure

1. Apply schema on Supabase via API migration startup (`sqlx::migrate!`) against Supabase DB URL.
2. Ensure role grants include `SET TRUE` on role memberships for `postgres`:
   - `GRANT app_worker TO postgres WITH SET TRUE;`
   - same for `app_reader`, `app_writer`, `app_migrator`, `kura`.
3. Copy data from VPS source DB to Supabase target:
   - source transaction `REPEATABLE READ, READ ONLY`
   - target `session_replication_role=replica`
   - truncate/copy all public tables except `_sqlx_migrations`
   - reset sequences
   - restore `session_replication_role=origin`
4. Configure production env (`docker/.env.production`):
   - set `KURA_API_DATABASE_URL` to Supabase session pooler URL
   - set `KURA_WORKER_DATABASE_URL` to Supabase session pooler URL
5. Redeploy services:
   - `docker compose -f docker/compose.production.yml --env-file docker/.env.production up -d kura-api kura-worker kura-proxy`

Note: `scripts/deploy.sh` now runs the migration drift gate automatically before starting services.

## 7. Post-Cutover Validation

1. Service health:
   - `docker ps` shows `kura-api`, `kura-worker`, `kura-proxy` healthy
   - `kura health` via gateway returns `status=ok`
2. Data parity spot-check:
   - `users`, `events`, `projections` counts match source snapshot
3. Worker startup and role switch:
   - no `permission denied to set role "app_worker"` in recent logs
4. Critical table presence:
   - `external_import_jobs` exists

## 8. Rollback Procedure (pending timed drill)

1. Set `KURA_API_DATABASE_URL` and `KURA_WORKER_DATABASE_URL` back to previous VPS Postgres URL.
2. Redeploy API/worker/proxy via compose.
3. Run health + key auth/projection smoke tests.
4. Confirm worker processes pending jobs.

## 9. Go/No-Go Gates

- Dry-run downtime <= 15 min: pending formal timed measurement
- Rollback <= 30 min: pending formal drill
- P0/P1 defects in cutover path: none currently open from runtime switch itself
- API health after cutover: pass
- Worker health after cutover: pass (after role grant fix)

## 10. Known Gaps

1. Formal rollback drill with measured recovery time is still missing.
2. Baseline artifact still needs explicit PITR retention, spend cap, and restore drill owner/date.
