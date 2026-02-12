# Design 025: Rollout Plan (Launch Import -> Post-Launch Connectors)

Status: implemented (2026-02-12)

## Objective

Roll out external data ingestion in controlled stages:

1. launch-safe file import first
2. connector integration later behind explicit gates
3. measurable quality and UX criteria at each stage

## Stages

### Stage 0: Internal Alpha

Scope:

- Team-only usage of `POST /v1/imports/jobs` + worker pipeline
- Supported formats: FIT/TCX/GPX
- Provider mapping matrix v1 (Garmin/Strava/TrainingPeaks)

Go criteria:

- Import success rate >= 95% across internal corpus
- No critical data-corruption incidents
- `quality_health` remains non-degraded for > 90% of runs

No-go triggers:

- Repeated `version_conflict` without remediation path
- Worker dead-letter growth > threshold
- Unexplained projection drift

### Stage 1: User Opt-In Import (Launch)

Scope:

- Opt-in file import for selected users
- Explicit "beta import" disclosure in product surfaces
- Quality receipts visible per import job

Go criteria:

- Successful import completion >= 97%
- Median import completion latency <= 30s
- `external_dedup_rejected_total / external_imported_total <= 2%`
- User-reported import confusion < agreed threshold

No-go triggers:

- Error-rate spike > 5% sustained over 24h
- `quality_health.status=degraded` for > 10% of opt-in users

### Stage 2: Connector Beta (Post-Launch)

Scope:

- Enable connector sync for selected providers/users
- Keep file import path as fallback
- Reuse same canonical contract + dedup engine

Go criteria:

- Connector sync parity with file imports on shared sample set
- Token/consent lifecycle verified (tm5.8 dependency)
- No regression in import quality metrics

### Stage 3: GA

Scope:

- Default-on connector onboarding where available
- File import remains available as manual recovery path

Go criteria:

- 2 consecutive release windows with stable KPIs
- No sev1 incidents in external ingestion path

## Feature Flags

Recommended flags (all default `false` outside internal):

- `external_import_jobs_enabled` (core async import path)
- `external_import_user_opt_in_enabled` (stage 1 access gate)
- `external_connector_beta_enabled` (stage 2 sync gate)
- `external_connector_provider_garmin_enabled`
- `external_connector_provider_strava_enabled`
- `external_connector_provider_trainingpeaks_enabled`
- `external_quality_gate_block_on_degraded` (policy hard-stop)

Flag policy:

- Stage advancement requires explicit flag review + rollback owner.
- Provider flags can be rolled back independently.

## KPI Set (Data Quality + UX)

Data quality KPIs:

- Import success rate
- Parse/mapping/validation error rates by provider+format
- Dedup outcomes:
  - `duplicate_skipped`
  - `idempotent_replay`
  - `rejected`
- External uncertainty metrics:
  - low-confidence mapped fields
  - unsupported fields total
  - temporal uncertainty hints

UX KPIs:

- Time-to-first-successful-import
- Median import completion latency
- Retry rate per user
- User-visible failure rate

Operational KPIs:

- Background job queue depth + dead-letter count
- Projection recompute latency after import
- API error rate for import endpoints

## Migration / Cutover Strategy

1. Keep legacy/manual data entry fully operational.
2. Introduce external imports as additive events (`external.activity_imported`).
3. Monitor mixed manual+external projections before expanding scope.
4. Promote providers one-by-one via provider-specific flags.

No destructive migration is required for rollout itself.

## Fallback / Incident Strategy

If connector path degrades:

1. Disable affected provider flag(s) immediately.
2. Keep file import path enabled for continuity.
3. Preserve queued jobs; stop new connector ingestion.
4. Investigate via import receipts + quality signals.
5. Re-enable only after KPI recovery in controlled cohort.

If import quality degrades globally:

1. Disable `external_import_user_opt_in_enabled`.
2. Continue read-only access to existing imported data.
3. Run repair/triage and re-open gradually via internal alpha gate.
