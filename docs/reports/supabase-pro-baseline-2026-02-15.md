# Supabase Baseline Report - 2026-02-15

Last updated: 2026-02-16

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
- Production compose requires explicit `KURA_API_DATABASE_URL` / `KURA_WORKER_DATABASE_URL` (no implicit local DB fallback for API/worker).

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

## Organization Billing Status

Observed via Management API on 2026-02-16:

- Organization: `gnimangxbapltvkrwjem` (`JZ`)
- Organization plan: `pro`
- Allowed release channels: `ga`, `preview`

## Addon Inventory (Management API)

`GET /v1/projects/{ref}/billing/addons` returned:

- `selected_addons` includes:
  - `compute_instance = ci_small` (Small)
  - `pitr = pitr_7` (7 days retention)
- `available_addons` includes:
  - `compute_instance` variants (`ci_micro` ... `ci_48xlarge_*`)
  - `pitr` variants (`pitr_7`, `pitr_14`, `pitr_28`)
  - `ipv4_default`
  - `custom_domain`, `auth_mfa_phone`, `auth_mfa_web_authn`, `log_drain`

## Backup / PITR Status

`supabase backups list --project-ref slawzzhovquintrsmfby` (2026-02-16):

- `region = eu-west-1`
- `walg_enabled = true`
- `pitr_enabled = true`
- `backups = []` at query time
- `physical_backup_data` present with earliest/latest backup unix timestamps

PITR activation sequence:

1. Initial PITR request returned `400`: `To enable PITR, you need to at least be on a small compute addon.`
2. Compute add-on updated to `ci_small` (`200`).
3. PITR add-on `pitr_7` applied successfully (`200`) after resize/cooldown.
4. Verification now reports `pitr_enabled = true`.

Restore drill evidence:

- `docs/reports/supabase-pitr-restore-drill-2026-02-16.md`

## Spend Guardrails API Coverage + Dashboard Evidence

Management API v1 currently exposes project billing add-ons, but no documented spend-alert / monthly hard-cap endpoints.

Documented billing path for this project:

- `GET/PATCH /v1/projects/{ref}/billing/addons`

Probed org-level billing paths (2026-02-15) all returned `404`:

- `/v1/organizations/{slug}/billing`
- `/v1/organizations/{slug}/billing/subscription`
- `/v1/organizations/{slug}/billing/alerts`
- `/v1/organizations/{slug}/billing/usage`

Dashboard configuration (verified 2026-02-16):

- Spend alerts: `50%`, `80%`, `95%` monthly budget thresholds.
- Monthly hard cap: configured in Supabase Billing dashboard (value managed operationally, not committed in repo).
- Owner: `jonzim-cmd`
- Escalation: `jonzim-cmd` -> launch room -> rollback owner (`jonzim-cmd`)

## Guardrail Gate Status

1. PITR gate (`pitr_enabled=true`): **PASS** (`true`, `pitr_7` selected).
2. Spend alerts 50/80/95 + monthly hard cap configured: **PASS** (dashboard configuration verified 2026-02-16).
3. Billing-plan readiness for paid guardrails: **PASS** (organization plan is `pro`).

## Required Manual Actions Before Public Launch

1. Keep periodic PITR restore drills and attach evidence reports.
2. Re-validate spend-alert thresholds and hard-cap settings monthly or after billing-owner changes.
