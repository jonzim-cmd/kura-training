# Design 039: Adaptive Abuse Detection + Rate Shaping V1

Status: implemented (2026-02-12)

## Goal

Add adaptive abuse controls on `/v1/agent/*` that activate only on risk signals,
instead of applying stricter global limits to all users.

This design targets:

- extraction-like burst behavior
- endpoint enumeration patterns
- repeated denied probes
- context scraping bursts

## Scope

- Runtime middleware on agent routes.
- Signal evaluation from recent per-user access telemetry.
- Progressive response actions (`allow`, `throttle`, `block`).
- Cooldown and recovery behavior.
- Telemetry persistence for false-positive and UX impact analysis.

## Signal Window and Taxonomy

Signals are computed from the most recent 60 seconds of `api_access_log` entries
for a user on `/v1/agent/*`:

- `burst_rate_60s`
- `denied_ratio_spike_60s`
- `endpoint_enumeration_pattern_60s`
- `context_scrape_burst_60s`
- `write_burst_60s`

Thresholds are explicit constants in
`api/src/middleware/adaptive_abuse.rs`.

## Risk Scoring and Actions

Each signal contributes weighted score points.

Decision model:

- `score < THROTTLE_SCORE_THRESHOLD` => `allow`
- `THROTTLE_SCORE_THRESHOLD <= score < BLOCK_SCORE_THRESHOLD` => `throttle`
- `score >= BLOCK_SCORE_THRESHOLD` => `block`

Shaping is progressive:

- throttle adds adaptive delay (`150-500ms`)
- block returns `429` with `retry-after`

## Cooldown and Recovery

User-scoped cooldown state is maintained in-memory:

- throttle sets/extends short cooldown
- block sets/extends longer cooldown
- during active cooldown, otherwise-allowed traffic is still throttled
- expired cooldown produces a single recovery telemetry transition

This avoids instant oscillation and provides deterministic decay behavior.

## Telemetry and Measurement

A dedicated infra table persists adaptive-abuse decisions:

- `security_abuse_telemetry` (migration `20260227000001_security_abuse_telemetry.sql`)

Captured fields include:

- action and risk score
- active signals and request-window metrics
- cooldown state
- response status/time
- `false_positive_hint`
- `ux_impact_hint` (`none|delayed|blocked`)

This supports post-hoc measurement of:

- false-positive tendency under specific signals
- UX impact (latency and blocked requests)
- cooldown efficiency and recovery stability

## Runtime Integration

- Middleware module: `api/src/middleware/adaptive_abuse.rs`
- Export: `api/src/middleware/mod.rs`
- Route wiring: `api/src/main.rs` on `routes::agent::router()`

Agent responses also expose shaping headers:

- `x-kura-abuse-action`
- `x-kura-abuse-score`
- `x-kura-abuse-signals`
- `x-kura-abuse-cooldown-until` (when active)

## Residual Risks / Non-Goals

- Cooldown state is process-local in V1 (no cross-instance shared state).
- V1 focuses on `/v1/agent/*` only; non-agent route shaping is out of scope.
- Signal scoring is deterministic heuristic logic, not model-based anomaly detection.
