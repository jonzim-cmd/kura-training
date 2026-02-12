# Design 018: Temporal Normalization for External Events

Status: implemented (2026-02-12)

## Goal

Normalize external time information so day/week/session projections remain stable
across timezone changes, device clock drift, and midnight boundaries.

## Canonical Timestamp Policy

Per event, workers derive one canonical UTC timestamp (`timestamp_utc`) using:

1. provider/source timestamps (`source_timestamp*`, `provider_timestamp*`, `start_time*`, `occurred_at`)
2. device timestamps (`device_timestamp`, `device_time`, `device_local_time`, `recorded_at_local`)
3. generic timestamp fields (`timestamp`)
4. fallback: event-store timestamp (`events.timestamp`)

If a candidate timestamp is naive (no offset), workers apply an explicit timezone
hint when present (`source_timezone`, `provider_timezone`, `device_timezone`,
`timezone`, `time_zone`).

## Timezone and Day/Week Semantics

- Day/week grouping always uses user timezone context (`preference.set` timezone),
  with explicit UTC assumption when missing.
- Local day and ISO week are computed from canonical timestamp, not raw event-store
  timestamp.
- This keeps projections joinable and prevents date drift when imported payloads
  carry source-side timestamps.

## Drift and Conflict Handling

Workers classify temporal uncertainty as conflict tags:

- `provider_device_drift`: provider and device timestamps differ beyond threshold
- `event_store_drift`: canonical timestamp differs from event-store timestamp
- `naive_timestamp_assumed_timezone`: naive timestamp required timezone assumption

These are recorded in projection `data_quality.temporal_conflicts`.

## Session Boundary Rules (Fallback Without `session_id`)

Default remains backward-compatible day-based grouping.
For midnight crossings, fallback session grouping keeps one session only when:

- gap between adjacent events <= 3 hours
- inferred session duration <= 8 hours

Otherwise a new session starts on the new local day.

This avoids splitting normal overnight sessions while preventing unrealistic
long-session merges.

## Backward Compatibility

- Explicit `metadata.session_id` always wins and bypasses fallback boundaries.
- Existing in-app/manual events without external timestamps keep previous behavior.
- New logic activates when external timestamp hints are present or midnight
  boundary handling is needed.
