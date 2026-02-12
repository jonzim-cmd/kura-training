# CT3 Security Baseline V1 Execution Plan (Compact-Safe)

## Purpose

This document is the authoritative execution and handoff reference for epic
`kura-training-ct3` (Agent Security Baseline V1). It is intentionally compact-safe:
any agent can resume from here when chat context is reduced or replaced.

## Scope

- Finish remaining CT3 work in a strict dependency-aware order.
- Keep default user experience low-friction while enforcing server-side security
  invariants and abuse resistance.
- Produce machine-readable behavior (errors, telemetry, and policy outcomes) so
  agents can self-correct.

## Out of Scope

- New product areas outside CT3.
- MCP server implementation details.
- Broad refactors unrelated to security baseline acceptance criteria.

## Current CT3 State

Completed:
- `kura-training-ct3.1` Threat model + security tiering.
- `kura-training-ct3.6` Server-side invariant enforcement review.

Open:
- `kura-training-ct3.2` Untrusted-agent boundary + context redaction contract.
- `kura-training-ct3.3` Least-privilege tokens + scope enforcement.
- `kura-training-ct3.4` Adaptive abuse detection + rate shaping (blocked by 3).
- `kura-training-ct3.5` Security telemetry + audit trail + kill switch (blocked by 3).
- `kura-training-ct3.7` Profile rollout + UX guardrail dashboard (blocked by 2/3/4/5).

## Execution Order (Authoritative)

1. `ct3.2`
2. `ct3.3`
3. `ct3.4`
4. `ct3.5`
5. `ct3.7`

Note: `ct3.4` and `ct3.5` can be developed in either order after `ct3.3`, but both
must be complete before `ct3.7`.

## Issue-by-Issue Done Criteria

### `ct3.2` Untrusted-Agent Boundary + Context Redaction

- Contract-first policy for allowed context fields and forbidden classes.
- `/v1/agent/context` emits only redacted/allowed data.
- Versioned contract surface with tests for allowed/forbidden payload slices.
- No regression in normal context retrieval UX.

### `ct3.3` Least-Privilege Tokens + Scope Enforcement

- Short-lived scoped tokens for agent operations.
- Deny-by-default scope checks enforced on server for protected operations.
- Audit visibility for allow/deny authorization outcomes.
- Expiry/rotation behavior tested without user-facing breakage.

### `ct3.4` Adaptive Abuse Detection + Rate Shaping

- Detection signals for extraction-like abuse patterns.
- Progressive, risk-triggered shaping/limits (not global hard limits).
- Cooldown/recovery path with measured false-positive impact.
- Telemetry hooks for abuse score transitions and shaping actions.

### `ct3.5` Security Telemetry + Audit Trail + Kill Switch

- Standardized security telemetry taxonomy and emission path.
- Operational kill switch to disable agent/token access quickly.
- Incident drill flow is testable and documented.
- Runbook-ready machine-readable traces for incident reconstruction.

### `ct3.7` Profile Rollout + UX Guardrail Dashboard

- Feature-flagged rollout for default/adaptive/strict profiles.
- Dashboard with paired security and UX KPIs.
- Data-backed rollout decisions with rollback guardrails.
- Default profile remains low-friction for normal users.

## Resume Protocol

When starting or resuming CT3 work:

1. Load environment:
   - `set -a && source .env && set +a`
2. Sync issue context:
   - `scripts/bd-safe.sh show kura-training-ct3 --json`
   - `scripts/bd-safe.sh blocked --json`
3. Verify working tree:
   - `git status --short`
4. Continue with next open issue in authoritative order.
5. Update issue notes using the compact-safe template below before closing.

## Compact-Safe Notes Template (Per Issue)

Every completed CT3 issue should include in bead notes:

- `implemented artifacts`: concrete file list.
- `tests`: exact commands run.
- `residual risks/non-goals`: explicit boundaries.
- `follow-up-issues`: exact IDs.
- `rollback/disable`: practical revert/disable path.

## Quality Gates (Required for Code Changes)

Run all applicable gates before marking an issue complete:

- `set -a && source .env && set +a && ruff check workers/src/ workers/tests/`
- `set -a && source .env && set +a && PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/ -q --ignore=workers/tests/test_integration.py`
- `set -a && source .env && set +a && PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/test_integration.py -q`
- `set -a && source .env && set +a && cargo test --workspace`

## Collaboration Note

Parallel user work (for example web page changes) may occur in this repository.
CT3 execution must stay scoped to security baseline files and avoid unrelated edits.
