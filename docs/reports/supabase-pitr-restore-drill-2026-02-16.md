# Supabase PITR Restore Drill - 2026-02-16

## Objective

Validate that PITR is enabled and a restore command can be executed with measurable recovery evidence.

## Preconditions

- Project: `slawzzhovquintrsmfby`
- Organization plan: `pro`
- Selected add-ons:
  - `compute_instance = ci_small`
  - `pitr = pitr_7`
- PITR status before drill: `pitr_enabled = true`
- Runtime health before drill:
  - gateway health: `{ "status": "ok", "version": "0.1.0" }`
  - key table counts:
    - `users = 1`
    - `events = 270`
    - `projections = 35`
    - `oauth_clients = 2`
    - `user_identities = 1`
    - `api_keys = 1`

## Drill Execution

- Start time (unix): `1771224587`
- Restore target (unix): `1771224287`
- Restore target (UTC): `2026-02-16T06:44:47Z`
- Command: `supabase backups restore --project-ref slawzzhovquintrsmfby --timestamp 1771224287 --yes`
- CLI response: `Started PITR restore: slawzzhovquintrsmfby`
- Status polling:
  - first poll state: `ACTIVE_HEALTHY`
- End time (unix): `1771224593`
- Measured recovery duration: `6s`

## Post-Drill Verification

- PITR status after drill: `pitr_enabled = true`
- Runtime health after drill:
  - gateway health: `{ "status": "ok", "version": "0.1.0" }`
- Key table counts after drill (unchanged):
  - `users = 1`
  - `events = 270`
  - `projections = 35`
  - `oauth_clients = 2`
  - `user_identities = 1`
  - `api_keys = 1`

## Result

PITR is enabled and restore drill evidence is captured with successful service recovery and no observed key-data drift.
