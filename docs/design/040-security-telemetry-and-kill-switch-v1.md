# Design 040: Security Telemetry + Audit Trail + Kill Switch V1

Status: implemented (2026-02-12)

## Goal

Provide an operationally usable security response loop:

- standardized security telemetry persistence
- explicit audit trail for incident actions
- operator-controlled kill switch that can disable agent access fast

## Scope

- Kill-switch state and audit storage.
- Admin endpoints to activate/deactivate and inspect status/audit.
- Runtime kill-switch enforcement on `/v1/agent/*`.
- Integration with adaptive abuse telemetry from CT3.4.

## Data Model

### `security_kill_switch_state`

Singleton table (`id=TRUE`) storing current switch state:

- `is_active`
- `reason`
- `activated_at`
- `activated_by`
- `updated_at`

### `security_kill_switch_audit`

Append-only incident audit stream:

- actions: `activated`, `deactivated`, `blocked_request`
- actor/target IDs
- path/method/reason
- metadata JSON for context

### `security_abuse_telemetry` (from CT3.4)

Standardized runtime signal/action metrics for abuse shaping and UX impact.

## API Contract

Admin-only endpoints:

- `GET /v1/admin/security/kill-switch`
- `POST /v1/admin/security/kill-switch/activate`
- `POST /v1/admin/security/kill-switch/deactivate`
- `GET /v1/admin/security/kill-switch/audit`
- `GET /v1/admin/security/telemetry/abuse`

Kill-switch enforcement path:

- middleware blocks `/v1/agent/*` while active
- response: structured `403` with `field=security.kill_switch`
- response header: `x-kura-kill-switch: active`

## Operational Behavior

- kill switch active => all agent endpoints blocked immediately at middleware.
- each blocked request emits `blocked_request` audit entry.
- activation/deactivation writes explicit audit entries with operator identity.

## Incident Runbook (V1)

1. Inspect status: `GET /v1/admin/security/kill-switch`
2. Activate with reason:
   `POST /v1/admin/security/kill-switch/activate`
3. Confirm blocked requests via:
   - API responses (`x-kura-kill-switch`)
   - `GET /v1/admin/security/kill-switch/audit`
4. Investigate abuse telemetry (`security_abuse_telemetry`) and root cause.
5. Deactivate with reason:
   `POST /v1/admin/security/kill-switch/deactivate`
6. Verify agent access restored and review final audit timeline.

## Drill / Test Procedure

Recommended controlled drill:

1. Call activate endpoint as admin (with synthetic incident reason).
2. Execute an authenticated `/v1/agent/context` request and verify `403`.
3. Fetch audit endpoint and verify `activated` + `blocked_request`.
4. Deactivate and verify `/v1/agent/context` succeeds again.
5. Record drill timestamp and findings.

## Residual Risks / Non-Goals

- Kill switch is route-scoped to `/v1/agent/*` in V1 (not global API lock).
- V1 does not auto-trigger external paging/alert systems.
- Middleware state checks are intentionally lightweight and may be tuned further
  for high-throughput deployments.
