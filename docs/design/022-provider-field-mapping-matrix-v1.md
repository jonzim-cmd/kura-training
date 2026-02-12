# Design 022: Garmin/Strava/TrainingPeaks Mapping Matrix v1

Status: implemented (2026-02-12)

## Goal

Define explicit field-level mapping from provider payloads into
`external_activity.v1`, including conversions, confidence, and unsupported
source fields.

Implementation source:
`workers/src/kura_workers/external_mapping_matrix.py`

## Provider Matrices

### Garmin v1 (`garmin-v1`)

| Canonical Field | Source Path | Transform | Units | Notes |
|---|---|---|---|---|
| `workout.workout_type` | `activity.type` | `identity` | n/a | activity category |
| `workout.duration_seconds` | `summary.duration_s` | `identity` | `s -> s` |  |
| `workout.distance_meters` | `summary.distance_m` | `identity` | `m -> m` |  |
| `workout.calories_kcal` | `summary.energy_kj` | `kj_to_kcal` | `kJ -> kcal` | confidence 0.95 |
| `session.started_at` | `activity.start_time` | `identity` | n/a | ISO timestamp |
| `session.ended_at` | `activity.end_time` | `identity` | n/a | ISO timestamp |
| `session.timezone` | `activity.timezone` | `identity` | n/a | timezone hint |

Unsupported v1:

- `summary.ground_contact_balance`

### Strava v1 (`strava-v1`)

| Canonical Field | Source Path | Transform | Units | Notes |
|---|---|---|---|---|
| `workout.workout_type` | `type` | `identity` | n/a | activity type |
| `workout.duration_seconds` | `moving_time` | `identity` | `s -> s` |  |
| `workout.distance_meters` | `distance` | `identity` | `m -> m` |  |
| `workout.calories_kcal` | `kilojoules` | `kj_to_kcal` | `kJ -> kcal` | confidence 0.92 |
| `session.started_at` | `start_date` | `identity` | n/a | ISO timestamp |
| `session.timezone` | `timezone` | `identity` | n/a | timezone hint |

Unsupported v1:

- `suffer_score`

### TrainingPeaks v1 (`trainingpeaks-v1`)

| Canonical Field | Source Path | Transform | Units | Notes |
|---|---|---|---|---|
| `workout.workout_type` | `workout.type` | `identity` | n/a | activity type |
| `workout.duration_seconds` | `workout.totalTimeMinutes` | `minutes_to_seconds` | `min -> s` |  |
| `workout.distance_meters` | `workout.distanceKm` | `km_to_meters` | `km -> m` |  |
| `workout.calories_kcal` | `workout.energyKj` | `kj_to_kcal` | `kJ -> kcal` | confidence 0.90 |
| `session.started_at` | `workout.startTime` | `identity` | n/a | ISO timestamp |
| `session.ended_at` | `workout.endTime` | `identity` | n/a | ISO timestamp |
| `session.timezone` | `workout.timezone` | `identity` | n/a | timezone hint |

Unsupported v1:

- `workout.normalizedPower`

## Conversion Rules

- `minutes_to_seconds`: `value * 60`
- `km_to_meters`: `value * 1000`
- `miles_to_meters`: `value * 1609.344`
- `kj_to_kcal`: `value * 0.239005736`

## Provenance and Uncertainty

Every mapped field writes `provenance.field_provenance`:

- `source_path`
- `confidence`
- `status = mapped`
- `transform`
- original and normalized units

Unsupported paths are emitted to `provenance.unsupported_fields` and are
never silently treated as trusted canonical fields.
