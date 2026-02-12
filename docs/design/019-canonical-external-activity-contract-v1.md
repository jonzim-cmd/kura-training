# Design 019: Canonical External Activity Contract v1

Status: implemented (2026-02-12)

## Goal

Provide a provider-agnostic, versioned contract for external training activities
so Garmin/Strava/TrainingPeaks inputs can be mapped into one stable shape before
core ingestion.

Contract identifier: `external_activity.v1`

## Contract Shape (v1)

Top-level object:

- `contract_version` (required, literal `external_activity.v1`)
- `source` (required)
- `workout` (required)
- `session` (required)
- `sets` (optional, default `[]`)
- `provenance` (required)

### Source Layer (required fields)

- `provider` (required)  
- `provider_user_id` (required)  
- `external_activity_id` (required)  
- `ingestion_method` (required: `file_import|connector_api|manual_backfill`)  

Optional source fields:

- `external_event_version`
- `raw_payload_ref`
- `imported_at`

The source layer is the canonical identity anchor for dedup/idempotency and
must survive all adapter transformations unchanged.

### Workout Slice

Required:

- `workout_type`

Optional:

- `title`
- `sport`
- `duration_seconds`
- `distance_meters`
- `energy_kj`
- `calories_kcal`

### Session Slice

Required:

- `started_at`

Optional:

- `ended_at` (must be `>= started_at` when present)
- `timezone`
- `local_date` (ISO date)
- `local_week` (ISO week)
- `session_id`

### Set Slice (`sets[]`)

Required per set entry:

- `sequence` (positive, unique within one activity)
- `exercise`

Optional per set entry:

- `exercise_id`
- `set_type`
- `reps`
- `weight_kg`
- `duration_seconds`
- `distance_meters`
- `rest_seconds`
- `rpe`
- `rir`

## Provenance Layer

Required:

- `mapping_version`
- `mapped_at`

Optional:

- `source_confidence` (`0..1`)
- `field_provenance` (per-field source + confidence + status)
- `unsupported_fields`
- `warnings`

Per-field provenance entries support:

- `source_path` (required)
- `confidence` (required, `0..1`)
- `status` (`mapped|estimated|unsupported|dropped`)
- `transform` (optional)
- `unit_original` (optional)
- `unit_normalized` (optional)
- `notes` (optional)

## Validation Contract

Validation is implemented in
`workers/src/kura_workers/external_activity_contract.py` and enforced through
`validate_external_activity_contract_v1(...)`.

Core rules:

- version pin (`contract_version`)
- required field presence
- session time window sanity (`ended_at >= started_at`)
- unique `sets[].sequence` per activity
- numeric bounds (`>=0`, `rpe/rir <= 10`, confidence in `0..1`)

## Compatibility

- v1 is additive-safe: optional fields can be introduced without breaking
  required invariants.
- breaking changes require a new `contract_version`.
