# Supabase Pro Baseline Report - 2026-02-15

## Project

- Project ref: `slawzzhovquintrsmfby`
- Region: EU
- URL: `https://slawzzhovquintrsmfby.supabase.co`

## Endpoints

- Direct DB: `db.slawzzhovquintrsmfby.supabase.co:5432/postgres`
- Session pooler DB: `aws-1-eu-west-1.pooler.supabase.com:5432/postgres`

## Runtime Decision (actual)

- Production API and worker run on session pooler URL.
- Direct endpoint was not reachable from VPS network path at cutover time, so direct mode was not used in production runtime.
- Production compose now requires explicit `KURA_API_DATABASE_URL` / `KURA_WORKER_DATABASE_URL` (no implicit local DB fallback for API/worker).

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

Current status: TODO (not yet documented in repo with exact values).

Required follow-up:
1. Record PITR retention and restore window.
2. Record budget alerts and monthly spend cap.
3. Schedule restore drill owner + date.
