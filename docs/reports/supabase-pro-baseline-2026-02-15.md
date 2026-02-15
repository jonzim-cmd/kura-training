# Supabase Baseline Report - 2026-02-15

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

Observed via Management API on 2026-02-15:

- Organization: `gnimangxbapltvkrwjem` (`JZ`)
- Organization plan: `free`
- Allowed release channels: `ga`, `preview`

## Addon Inventory (Management API)

`GET /v1/projects/{ref}/billing/addons` returned:

- `selected_addons = []`
- `available_addons` includes:
  - `compute_instance` variants (`ci_micro` ... `ci_48xlarge_*`)
  - `pitr` variants (`pitr_7`, `pitr_14`, `pitr_28`)
  - `ipv4_default`
  - `custom_domain`, `auth_mfa_phone`, `auth_mfa_web_authn`, `log_drain`

## Backup / PITR Status

`supabase backups list --project-ref slawzzhovquintrsmfby` (2026-02-15):

- `region = eu-west-1`
- `walg_enabled = true`
- `pitr_enabled = false`
- `backups = []` at query time

PITR enablement attempt (2026-02-15):

- request: `PATCH /v1/projects/slawzzhovquintrsmfby/billing/addons` with `{"addon_type":"pitr","addon_variant":"pitr_7"}`
- response: `400`
- message: `Organization is not entitled to the selected PITR duration.`

## Spend Guardrails API Coverage

Management API v1 currently exposes project billing add-ons, but no documented spend-alert / monthly hard-cap endpoints.

Documented billing path for this project:

- `GET/PATCH /v1/projects/{ref}/billing/addons`

Probed org-level billing paths (2026-02-15) all returned `404`:

- `/v1/organizations/{slug}/billing`
- `/v1/organizations/{slug}/billing/subscription`
- `/v1/organizations/{slug}/billing/alerts`
- `/v1/organizations/{slug}/billing/usage`

## Guardrail Gate Status

1. PITR gate (`pitr_enabled=true`): **FAIL** (`false`).
2. Spend alerts 50/80/95 + monthly hard cap configured: **FAIL** (not available in current org state / not configured).
3. Billing-plan readiness for paid guardrails: **FAIL** (organization plan is `free`).

## Required Manual Actions Before Public Launch

1. Upgrade organization plan from `free` to a paid plan that entitles PITR and spend controls.
2. Configure spend alerts at 50%, 80%, 95% and set monthly hard cap in Supabase dashboard/billing.
3. Re-run PITR enablement check until `pitr_enabled=true`.
4. Run PITR restore drill and attach evidence to rollout report.
