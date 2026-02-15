# Supabase Pro Baseline Report - 2026-02-15

## Project

- Project ref: `slawzzhovquintrsmfby`
- Region: `eu-west-1` (West EU / Ireland)
- URL: `https://slawzzhovquintrsmfby.supabase.co`
- Status: `ACTIVE_HEALTHY`
- Postgres engine: `17.6.1.063` (Supabase release channel `ga`)

## Endpoints

- Direct DB: `db.slawzzhovquintrsmfby.supabase.co:5432/postgres`
- Session pooler DB: `aws-1-eu-west-1.pooler.supabase.com:5432/postgres`

## Auth Endpoints

- Issuer: `https://slawzzhovquintrsmfby.supabase.co/auth/v1`
- JWKS: `https://slawzzhovquintrsmfby.supabase.co/auth/v1/.well-known/jwks.json`
- Authorization endpoint: `https://slawzzhovquintrsmfby.supabase.co/auth/v1/oauth/authorize`
- Token endpoint: `https://slawzzhovquintrsmfby.supabase.co/auth/v1/oauth/token`

## Runtime Decision (actual)

- Production API and worker run on session pooler URL.
- Direct endpoint was not reachable from VPS network path at cutover time, so direct mode was not used in production runtime.
- Production compose now requires explicit `KURA_API_DATABASE_URL` / `KURA_WORKER_DATABASE_URL` (no implicit local DB fallback for API/worker).

## Transport Security

- SSL enforcement (database external connections): **enabled** (verified via Supabase CLI on 2026-02-15)

## Roles Created on Supabase

- `app_reader`
- `app_writer`
- `app_worker` (`BYPASSRLS`)
- `app_migrator`
- `kura`

Additional required fix during rollout:
- role memberships for `postgres` needed explicit `SET TRUE` to allow `SET ROLE app_worker`.

## Secrets Matrix (names only)

- `KURA_API_DATABASE_URL` - source: `docker/.env.production` on VPS - owner: ops
- `KURA_WORKER_DATABASE_URL` - source: `docker/.env.production` on VPS - owner: ops
- `KURA_WORKER_LISTEN_DATABASE_URL` - source: `docker/.env.production` on VPS - owner: ops (optional, direct LISTEN path)
- `KURA_API_KEY` - source: `docker/.env.production` on VPS - owner: ops
- `KURA_AGENT_MODEL_ATTESTATION_SECRET` - source: `docker/.env.production` on VPS - owner: ops

## Backup / PITR / Spend Guardrails

- Physical backup API status (`supabase backups list`):
  - `region = eu-west-1`
  - `walg_enabled = true`
  - `pitr_enabled = false`
  - `backups = []` at query time
- PITR enablement requires explicit operator action in Supabase project settings (CLI does not expose an enable command in current version).
- Spend guardrails are an operator-controlled setting in the dashboard; current CLI version does not expose a read endpoint for budget alerts/hard cap.

### Guardrail Policy (launch target)

1. PITR: enable before public launch and verify `pitr_enabled=true`.
2. Spend alerts: configure alert thresholds at 50%, 80%, 95% of monthly budget.
3. Monthly hard cap: configure organization-level spend cap before launch freeze.

### Restore Drill Window

- Window: **2026-02-20 10:00-10:30 UTC**
- Owner: `jonzim-cmd`
- Objective: execute PITR restore rehearsal and validate service recovery checklist.
