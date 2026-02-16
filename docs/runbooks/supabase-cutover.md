# Supabase Cutover Runbook (Strategie B: DB-only)

## 1. Scope

This runbook covers the launch-critical DB cutover from VPS Postgres to Supabase.
Auth strategy is fixed to `AUTH_STRATEGY=B`: auth logic remains in Kura API (`users`, `api_keys`, `oauth_*` stay source of truth).

## 2. Environment Baseline

- Supabase project ref: `slawzzhovquintrsmfby`
- Region: EU (`eu-west-1`)
- Auth issuer: `https://slawzzhovquintrsmfby.supabase.co/auth/v1`
- Auth JWKS: `https://slawzzhovquintrsmfby.supabase.co/auth/v1/.well-known/jwks.json`
- Primary DB name: `postgres`
- Direct endpoint (5432): `db.slawzzhovquintrsmfby.supabase.co`
- Session pooler endpoint (5432): `aws-1-eu-west-1.pooler.supabase.com`
- DB SSL enforcement: enabled

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
   - `docker compose -f docker/compose.production.yml --env-file docker/.env.production up -d kura-api kura-worker`
   - `docker compose -f docker/compose.production.yml --env-file docker/.env.production up -d --force-recreate kura-proxy`

Note: `scripts/deploy.sh` runs the migration drift gate automatically before starting services.

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
5. Auth compatibility regression (Strategie B, DB-only):
   - run `cargo test -p kura-api routes::auth::tests -- --nocapture` against Supabase DB URL
   - verify login, PKCE exchange, refresh rotation, and device token flows pass without Supabase Auth token issuance
   - evidence report: `docs/reports/supabase-auth-compatibility-2026-02-15.md`

## 8. Rollback Procedure

1. Set `KURA_API_DATABASE_URL` and `KURA_WORKER_DATABASE_URL` back to previous VPS Postgres URL.
2. Redeploy runtime:
   - `docker compose -f docker/compose.production.yml --env-file docker/.env.production up -d kura-api kura-worker`
   - `docker compose -f docker/compose.production.yml --env-file docker/.env.production up -d --force-recreate kura-proxy`
3. Run health + key auth/projection smoke tests.
4. Confirm worker processes pending jobs.
5. Roll-forward to Supabase after probe/fix with the same service sequence (API/worker, then forced proxy recreate).

## 9. Rollback Trigger Matrix

1. Trigger: sustained API 5xx after cutover (>= 5 minutes)
   - Action: execute full rollback procedure immediately
2. Trigger: worker cannot process jobs after role/grant verification
   - Action: rollback runtime DB URLs, then investigate Supabase role/session path offline
3. Trigger: auth regression in login/refresh/device/PKCE smoke suite
   - Action: rollback and freeze new writes until parity confirmed
4. Trigger: data integrity mismatch in key table spot checks (`users/events/projections/oauth_*`)
   - Action: rollback and run source-vs-target diff before reattempt

## 10. Monitoring & Alerts

Alert owner: `jonzim-cmd`  
Escalation path: `jonzim-cmd` -> launch room -> rollback owner (`jonzim-cmd`)  
Reaction SLA: acknowledge in `<= 5 min`, mitigation decision in `<= 15 min`

| Signal | Source | Alert Threshold | Owner | Reaction SLA |
| --- | --- | --- | --- | --- |
| Auth failure rate | `POST /v1/auth/email/login` responses | `>= 5` auth failures within 1 minute | `jonzim-cmd` | 5 min ack / 15 min decision |
| DB latency / connection errors | Supabase + API logs (`Circuit breaker open`, connection failures) | any DB connection error in 5 minutes OR sustained latency breach for 5 minutes | `jonzim-cmd` | 5 min ack / 15 min decision |
| API 5xx | `kura-proxy` status codes + gateway health probes | `>= 5` 5xx responses in 5 minutes OR healthcheck failing >= 2 minutes | `jonzim-cmd` | 5 min ack / 15 min decision |
| Worker dead jobs | `background_jobs` dead count + worker logs | `dead_jobs > 0` for 15 minutes OR repeated dead-job spikes in one hour | `jonzim-cmd` | 5 min ack / 15 min decision |

## 11. Alert Drill Evidence (2026-02-15)

Evidence report: `docs/reports/supabase-monitoring-drill-2026-02-15.md`

1. Auth failure-rate drill:
   - timestamp: 2026-02-15 22:33 UTC
   - result: six invalid login attempts returned six `401` responses in under one minute
   - threshold status: breach detected as expected
2. DB connection-error drill:
   - timestamp: 2026-02-15 22:31 UTC
   - result: API emitted `Circuit breaker open: Failed to retrieve database credentials` warnings (3 events)
   - threshold status: breach detected as expected
3. API 5xx drill:
   - timestamp: 2026-02-15 (preliminary rollback run)
   - result: proxy returned `502` after API recreation without proxy recreate
   - threshold status: breach detected as expected, mitigation validated (`--force-recreate kura-proxy`)
4. Worker dead-jobs signal validation:
   - timestamp: 2026-02-13
   - result: repeated dead projection jobs from schema drift (`external_import_jobs` missing)
   - threshold status: historical breach validates dead-job alert necessity

## 12. Go/No-Go Gates

| Gate | Threshold | Evidence | Status |
| --- | --- | --- | --- |
| Rollback readiness | rollback `<= 30 min` | timed rollback `73s` on 2026-02-15 | PASS |
| Restore readiness | restore-to-Supabase `<= 15 min` | timed restore `74s` on 2026-02-15 | PASS |
| Runtime health | API + worker healthy after cutover | healthy containers + gateway `status=ok` | PASS |
| Data integrity | key table spot checks unchanged | `users/events/projections/oauth_clients/user_identities/api_keys` parity | PASS |
| Auth compatibility | Strategy B auth tests pass | `cargo test -p kura-api routes::auth::tests` 15/15 pass | PASS |
| PITR gate | `pitr_enabled=true` | `pitr_enabled=true` with `pitr_7` + restore drill 2026-02-16 (`6s`) | PASS |
| Spend guardrail gate | alerts 50/80/95 + hard cap configured | dashboard configuration verified on 2026-02-16 (see baseline report) | PASS |

## 13. 24h Post-Cutover Checklist

1. Re-check API and worker health every hour for first 6h, then every 4h.
2. Confirm no sustained auth-failure spikes outside expected user mistakes.
3. Confirm no recurring DB connection warnings (`Circuit breaker open`, auth failures to pooler).
4. Confirm worker queue stays stable (no dead-job accumulation).
5. Re-run key data parity query set (`users/events/projections/oauth_*`) at +24h.
6. Record any anomalies and decision trail in launch log.

## 14. Communication Templates

Cutover start:

> `2026-02-15 HH:MM UTC` Cutover started. Write-freeze active. Runtime switch to Supabase in progress. Next status in 10 minutes.

Rollback triggered:

> `2026-02-15 HH:MM UTC` Rollback triggered due to `<trigger>`. API/worker DB URLs are being reverted to VPS Postgres. Recovery ETA: 15 minutes.

Cutover complete:

> `2026-02-15 HH:MM UTC` Cutover complete. API/worker healthy, data parity checks passed, monitoring watch window active for 24h.

## 15. Current Launch Blockers

1. None in this Supabase cutover track: PITR and spend guardrail gates are both `PASS` as of 2026-02-16.
2. Keep monthly billing-guardrail review (alerts/hard-cap) under the owner/escalation policy in section 10.
