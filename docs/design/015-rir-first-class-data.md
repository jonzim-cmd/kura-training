# Design 015: RIR as First-Class Data

Status: implemented (2026-02-11)

## Goal

Represent Reps-In-Reserve (RIR) as structured data in both logged sets and
training prescriptions so intensity intent and execution can be analyzed
consistently.

## Data Contract

- `set.logged.data.rir` stores observed set-level RIR (`0..10`, optional).
- `training_plan.*.sessions[].exercises[].target_rir` stores prescribed RIR (`0..10`, optional).
- If `target_rir` is missing but `target_rpe` exists, projection normalization
  infers `target_rir = 10 - target_rpe` and records
  `target_rir_source = inferred_from_target_rpe`.
- For historical `set.logged` events without `rir`, projections infer
  `rir` from `rpe` when available (`rir = 10 - rpe`) and mark
  `rir_source = inferred_from_rpe`.

## Backward Compatibility

- No database migration is required. Event payloads remain JSON and append-only.
- Existing events without RIR continue to project normally.
- Existing training plans without `target_rir` are preserved; normalization is
  additive and non-destructive.

## Backfill Strategy

1. Projection-only backfill (immediate):
   - infer RIR from historical RPE at read/projection time.
   - expose inferred source metadata in projections.
2. Optional canonical backfill (controlled):
   - if durable persistence is desired for selected sessions, emit
     `set.corrected` events to write explicit `rir` values with provenance.
   - keep backfill idempotent via event metadata idempotency keys.

## Queryability

- Exercise projections expose per-set `rir` (explicit or inferred).
- Training-plan projection exposes `target_rir` and `rir_targets` summary to
  support adherence analysis against executed sets.

