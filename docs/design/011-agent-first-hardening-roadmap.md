# Roadmap 011: Agent-First Hardening and Fluidity

Status: in_progress (2026-02-11)

## Goal

Stabilize the current agent-first platform for real multi-agent usage while
keeping the system fluid (non-rigid) and evolvable.

This roadmap is intentionally implementation-oriented so work can continue even
after context loss.

## Workstream A: Security and Trust Boundaries

- [x] A1. OAuth client binding
  - Add client registry in DB.
  - Validate `client_id` + `redirect_uri` on authorize GET and POST.
  - Keep loopback callback support for native CLI clients.
- [ ] A2. Auth flow hardening tests
  - Add tests for unknown/inactive clients and invalid redirect URIs.

## Workstream B: Projection Rule Reliability

- [x] B1. Rule liveness decoupled from projection existence
  - Ensure custom-rule recomputation is based on active rule events, not
    existing custom projections.
- [x] B2. Rule inspectability
  - Add API endpoint to list active projection rules for the authenticated user.

## Workstream C: Agent Contract Consistency

- [x] C1. Interview guide <-> coverage logic parity
  - Fix `communication_preferences` coverage handling.
  - Resolve `program.started` mismatch between guide and runtime logic.
- [x] C2. Contract tests
  - Add tests that fail on guide/event-convention/coverage drift.

## Workstream D: Data Access Correctness

- [x] D1. RLS-safe similarity checks
  - Ensure exercise similarity lookups use explicit user RLS context.

## Workstream E: Fluid-System Evolution (next iterations)

- [x] E1. Agent context bundle endpoint
  - Single fetch for `system_config + user_profile + key dimensions`.
- [x] E2. Rule lifecycle API extension
  - Validate/preview/apply/archive ergonomics for projection rules.
- [x] E3. Projection freshness SLA metadata
  - Expose lag and last processing state in projection responses.
- [x] E4. Optional dry-run simulation endpoint
  - Predict projection deltas before writing events.

## Workstream F: Self-Healing Data Plane (Decision 13)

- [x] F1. Invariant evaluator (read-only mode)
  - Implement typed invariant checks over events + projections.
  - Emit quality issues without automatic mutation.
- [x] F2. Quality health projection
  - Add `quality_health/overview` projection with score, open issues, and severity breakdown.
- [x] F3. Repair proposal + simulation bridge
  - Generate proposed compensating event batches.
  - Run all proposals through `/v1/events/simulate` before apply.
- [ ] F4. Agent protocol hardening
  - Enforce write receipts + read-after-write verification before "saved" claims.
  - Add explicit uncertainty/deferred markers for low-confidence facts.
- [ ] F5. Autonomous low-risk repair tier
  - Auto-apply only deterministic Tier A repairs.
  - Keep higher-risk repairs confirmation-gated.
- [ ] F6. Integrity SLOs + autonomy throttling
  - Define and monitor data-quality SLOs.
  - Reduce agent autonomy automatically on sustained SLO regressions.

## Current execution order

1. A1
2. B1
3. C1
4. D1
5. B2
6. C2

## Notes

- Keep event model flexible and append-only.
- Prefer transparent, inspectable adaptation over implicit magic.
- For every new dynamic behavior: make it reversible, observable, and testable.
