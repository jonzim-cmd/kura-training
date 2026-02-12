# Extraction Calibration + Drift Pipeline V1 (2zc.5)

Date: 2026-02-12  
Owner: Team (JZ + Codex)  
Issue: `kura-training-2zc.5`

## Goal

Close the loop for free-text extraction confidence by measuring how often
`evidence.claim.logged` predictions are later contradicted by corrections/retractions.

## Calibration Dataset

Source events:

- `evidence.claim.logged` (prediction: `claim_type`, `confidence`, `parser_version`)
- `set.corrected` (contradiction signal for claim field)
- `event.retracted` (contradiction signal for target event)

Labeling rule (V1):

- `label=0` (incorrect) if target event is retracted or corrected on the claim field.
- `label=1` (correct) otherwise.

## Metrics

Computed per `(claim_class, parser_version)` and per period (`day`, `week`):

- `brier_score`
- `precision_high_conf` (confidence >= threshold)
- `recall_high_conf`
- `sample_count`, `correct_count`, `incorrect_count`
- confidence-band reliability slices (`high|medium|low`)

## Drift

For each stream `(period_granularity, claim_class, parser_version)`:

- compare current Brier score to previous period
- `drift_alert` when `delta_brier >= threshold`

## Persistence

New tables:

- `extraction_calibration_metrics`
- `extraction_calibration_runs`
- `extraction_underperforming_classes`

Weekly underperformers are materialized for direct backlog/policy consumption.

## Policy Integration

`quality_health` now includes extraction calibration status in projection output and autonomy policy:

- `autonomy_policy.calibration_status`
- degraded calibration disables auto-repair
- monitor calibration throttles auto-repair (requires confirmation)

API session-audit auto-repair gate respects this via `calibration_status`.

## Non-Goals (V1)

- No model-based relabeling; only deterministic correction/retraction-derived labels.
- No per-user adaptive thresholding.
- No automatic issue generation from underperforming classes (handled by `2zc.3`).

## Rollback / Disable

- Remove `refresh_extraction_calibration(...)` call from nightly handler.
- Ignore calibration status in autonomy policy derivation to restore previous behavior.
