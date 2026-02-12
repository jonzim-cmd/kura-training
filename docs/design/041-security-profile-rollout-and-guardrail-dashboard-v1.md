# Design 041: Security Profile Rollout + Guardrail Dashboard V1

Status: implemented (2026-02-12)

## Goal

Roll out `default`, `adaptive`, and `strict` security profiles in a controlled,
feature-flagged way while keeping UX and security outcomes visible in one place.

## Rollout Mechanics

### Feature flags

Rollout state is persisted in `security_profile_rollout`:

- `default_profile`
- `adaptive_rollout_percent`
- `strict_rollout_percent`
- `updated_by`, `updated_at`, `notes`

Constraint: `adaptive + strict <= 100`.

### User overrides

`security_profile_user_overrides` provides deterministic per-user exceptions for:

- canary users
- incident containment
- temporary strict enforcement

### Profile resolution

For every authenticated agent request:

1. Check per-user override.
2. Else map user into deterministic rollout bucket (`0-99`).
3. Assign profile by configured percentages.
4. Fall back to adaptive profile if resolution fails.

## Runtime Behavior by Profile

Adaptive abuse middleware now applies profile-specific tuning:

- thresholds (`throttle` / `block`)
- cooldown durations
- shaping delay

`default` profile is intentionally high-threshold and low-friction.
`strict` profile is intentionally aggressive.

## Guardrail Dashboard

Admin endpoint: `GET /v1/admin/security/guardrails/dashboard`

Returns paired KPIs per profile (24h window):

- security: blocked/throttled volume, false-positive hints
- UX: average response time, success rate

Also includes:

- current rollout config
- kill-switch active state

## Telemetry Contract

`security_abuse_telemetry` now stores resolved `profile` on every agent request
to make profile-level guardrail analysis possible.

## Decision Records

Data-backed rollout decisions are captured in
`security_profile_rollout_decisions` with:

- decision
- rationale
- metrics snapshot
- rollout config snapshot at decision time

Endpoints:

- `POST /v1/admin/security/profiles/decisions`
- `GET /v1/admin/security/profiles/decisions`

## API Surface (Admin)

- `GET/POST /v1/admin/security/profiles/rollout`
- `POST/DELETE /v1/admin/security/profiles/overrides/{user_id}`
- `GET /v1/admin/security/guardrails/dashboard`
- `POST/GET /v1/admin/security/profiles/decisions`

## Residual Risks / Non-Goals

- Profile selection is deterministic and simple; no dynamic ML policy here.
- Dashboard uses recent telemetry aggregates and is not a historical BI system.
- Multi-region rollout propagation concerns are deferred to later iterations.
