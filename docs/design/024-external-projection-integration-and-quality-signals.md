# Design 024: Projection Integration + Quality Signals for External Data

Status: implemented (2026-02-12)

## Goal

Make imported external activities visible in core projection outputs and expose
explicit quality signals (instead of silently treating uncertain imports as
fully trusted).

## Projection Integration

### training_timeline

`training_timeline` now reacts to `external.activity_imported` and merges
external sessions into day/week/session aggregates alongside manual `set.logged`
data.

Integration behavior:

- external sessions are transformed into synthetic set-like rows for aggregation
- `recent_sessions[]` includes optional source metadata:
  - `source_provider`
  - `source_type` (`manual` or `external_import`)
- mixed manual + external timelines are aggregated in one consistent projection

## Quality Signals

### training_timeline `data_quality`

New external-specific quality counters:

- `external_imported_sessions`
- `external_source_providers`
- `external_low_confidence_fields`
- `external_unit_conversion_fields`
- `external_unsupported_fields_total`
- `external_temporal_uncertainty_hints`
- `external_dedup_actions` (`duplicate_skipped`, `idempotent_replay`, `rejected`)

### quality_health

`quality_health` now evaluates external import signals under invariant family
`INV-009`:

- `external_unsupported_fields`
- `external_low_confidence_fields`
- `external_temporal_uncertainty`
- `external_dedup_rejected`

Metrics include import totals, dedup outcomes, and uncertainty counters.

## Triggering / Freshness

For imports that do not create a new event (e.g. dedup skip/reject), the import
worker enqueues a synthetic projection update with event type
`external.import.job` so `quality_health` still refreshes from
`external_import_jobs` receipts.

## Safety Rule

Uncertain imported fields are surfaced as quality signals and metrics. They are
not silently upgraded to fully trusted canonical certainty.
