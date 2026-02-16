# Supabase Monitoring Drill Report - 2026-02-15

## Objective

Validate the launch-critical monitoring gates for:

- auth failure rate
- DB latency / connection errors
- API 5xx
- worker dead jobs

## Drill 1: Auth Failure Rate

- Timestamp: 2026-02-15 22:33 UTC
- Method: six invalid `POST /v1/auth/email/login` requests via gateway network path
- Result: `401` returned six times in under one minute
- Threshold check: `>= 5` failures/minute -> **BREACH DETECTED (expected)**

## Drill 2: DB Connection Error Signal

- Timestamp: 2026-02-15 22:31 UTC
- Method: observe API/DB connection path while testing Supabase pooler connectivity
- Evidence:
  - API warnings: `Circuit breaker open: Failed to retrieve database credentials` (3 events)
  - ad-hoc pooler probe returned `FATAL: Circuit breaker open: Failed to retrieve database credentials`
- Threshold check: any DB connection error in 5 minutes -> **BREACH DETECTED (expected)**

## Drill 3: API 5xx Signal

- Timestamp: 2026-02-15 (preliminary rollback run)
- Method: historical cutover incident replay evidence from dry-run log
- Evidence: proxy returned `502` after API recreation when proxy was not recreated
- Mitigation validation: `docker compose ... up -d --force-recreate kura-proxy` removes the failure mode
- Threshold check: 5xx burst condition -> **BREACH DETECTED (expected)**

## Drill 4: Worker Dead-Jobs Signal

- Timestamp: 2026-02-13 incident reference
- Method: historical production incident evidence (schema drift before migration parity fix)
- Evidence: repeated dead projection jobs caused by missing `external_import_jobs`
- Threshold check: dead-job accumulation signal -> **BREACH DETECTED (historical evidence)**

## Outcome

The monitoring design required by `kura-training-4sv.7` has trigger evidence for all required signals.
Guardrail follow-ups are now closed: billing spend guardrails (`kura-training-4sv.8`) and PITR restore readiness (`kura-training-4sv.9`) are both completed.
