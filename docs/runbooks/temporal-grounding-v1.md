# Temporal Grounding v1 Runbook

## Purpose

This runbook defines how Kura keeps agent time semantics deterministic across chat turns.
It is designed to remain useful after context compaction by anchoring implementation intent,
operational checks, and rollback steps in one place.

## Problem Statement

Agents can answer with stale temporal assumptions (for example treating Sunday as "directly after Friday")
when relative terms (`today`, `yesterday`, `last Friday`) are not grounded to a fresh server-side clock.
This causes planning drift, wrong recovery windows, and low trust.

## Decision Summary (Why)

- Time-sensitive reasoning must not depend on model memory or implicit prompt state.
- Kura must provide deterministic per-turn temporal context from the server.
- High-impact temporal writes must carry verifiable temporal basis metadata.
- Missing timezone must be explicit (`UTC` assumption disclosure), never hidden.

## Runtime Contract (How)

### Read path: `/v1/agent/context`

`meta.temporal_context` is the source of truth:

- `schema_version` (`temporal_context.v1`)
- `now_utc`
- `now_local`
- `today_local_date`
- `weekday_local`
- `timezone`
- `timezone_source` (`preference|assumed_default`)
- `timezone_assumed`
- `assumption_disclosure` (optional)
- `last_training_date_local` (optional)
- `days_since_last_training` (optional)

### Write path: `/v1/agent/write-with-proof`

For planning/coaching writes, `intent_handshake.temporal_basis` is required and validated:

- `schema_version` (`temporal_basis.v1`)
- `context_generated_at`
- `timezone`
- `today_local_date`
- Optional: `last_training_date_local`, `days_since_last_training`

Validation gates:

- freshness window
- timezone match
- local-date match
- day-delta consistency when anchors are present

## Integration Rules

1. Fetch fresh `/v1/agent/context` before high-impact/temporal writes.
2. Carry `meta.temporal_context` into `intent_handshake.temporal_basis`.
3. Do not derive day deltas in model text; rely on server-computed fields.
4. Keep regression corpus coverage for natural phrases:
   - same day
   - plus five-hour gap
   - day rollover
   - week rollover
   - timezone switch while traveling

## Failure Modes and Handling

- **Missing temporal basis**
  - Symptom: validation error on `intent_handshake.temporal_basis`.
  - Action: refetch `/v1/agent/context`, retry with updated basis.
- **Stale temporal basis**
  - Symptom: validation error on `context_generated_at`.
  - Action: regenerate handshake with fresh context.
- **Timezone mismatch**
  - Symptom: basis timezone differs from current context timezone.
  - Action: refresh context, avoid reusing old cached basis.
- **Assumed timezone share is high**
  - Symptom: telemetry anomaly indicates UTC fallback overuse.
  - Action: prompt users to set explicit timezone preference.

## Observability

Monitor:

- `/v1/admin/agent/telemetry/overview`
  - `requests.context_read_coverage_pct`
  - `quality_health.assumed_timezone_context_share_pct`
- `/v1/admin/agent/telemetry/anomalies`
  - `context_read_coverage_low`
  - `assumed_timezone_share_high`

## Rollout

1. Deploy API + worker changes together.
2. Verify architecture specs and runtime tests pass.
3. Verify `meta.temporal_context` in live `/v1/agent/context` responses.
4. Verify high-impact write path rejects stale/missing temporal basis.
5. Observe telemetry for one full day window (24h default).

## Rollback

If severe regressions occur:

1. Revert API commit that enforces temporal basis validation.
2. Revert worker readiness timezone-first change only if projection correctness regresses.
3. Keep telemetry fields if possible (safe additive schema) for diagnosis.
4. Re-run architecture and runtime contract tests before re-enable.

## References

- Issues: `kura-training-2ut`, `kura-training-2ut.1` ... `kura-training-2ut.8`
- Architecture specs:
  - `tests/architecture/test_60_agent_contract_coverage_matrix.py`
  - `tests/architecture/test_112_temporal_grounding_contract.py`
  - `tests/architecture/test_113_temporal_phrase_regression_contract.py`
- Key implementation files:
  - `api/src/routes/agent.rs`
  - `api/src/routes/agent/write_verification.rs`
  - `api/src/routes/agent_telemetry.rs`
  - `workers/src/kura_workers/handlers/readiness_inference.py`
  - `workers/src/kura_workers/system_config.py`
  - `mcp-runtime/src/lib.rs`
