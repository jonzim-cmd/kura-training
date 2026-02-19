# Training Load zjm3 Relative-Intensity Update

Date: 2026-02-19

## Scope

- Added first-class `relative_intensity` support (`value_pct`, `reference_type`, `reference_value`, `reference_measured_at`, `reference_confidence`) across:
  - `session.logged` block payloads
  - external import canonical contract + mapping
  - session block expansion and load aggregation

## Modality-Specific Behavior

- Strength: `%e1RM/%1RM` style references are accepted via `reference_type=e1rm|one_rm`.
- Sprint/Endurance: `%MSS`, `%critical_speed`, `%critical_power`, `%MAS/vVO2max`, `%ASR` via corresponding `reference_type`.
- Plyometric: `%jump_height` references are accepted when available.

## Fallback and Uncertainty Rules

- Internal response resolver order is now:
  1. `relative_intensity`
  2. `power`
  3. `heart_rate`
  4. `pace`
  5. `rpe` / `rir`
- If relative-intensity references are stale or incomplete, the system falls back deterministically to sensor/subjective signals and increases uncertainty.
- Endurance RPE guidance remains valid as fallback context:
  - RPE 6-7: threshold-oriented
  - RPE 8-9: VO2max-oriented
  - RPE 9-10: anaerobic-capacity-oriented
  - These are guidance bands, not hard physiological truth.

## Modality Mapping Hardening

- Exercise-based modality assignment is now observable with diagnostics:
  - assignment source tracking (`block_type`, exercise mapping, heuristic fallback)
  - unknown distance-based `exercise_id` counters to prevent silent endurance bias.
