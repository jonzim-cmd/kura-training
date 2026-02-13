# Model-Aware Agent Runtime V1: Execution Plan (wvp.1 -> wvp.2 -> wvp.3)

Date: 2026-02-13
Owner: Team (Human + Agent)
Status: draft-ready-for-implementation
Epic: `kura-training-wvp`

## Why this exists

Different model runtimes produce different quality profiles. If runtime autonomy is static,
users see inconsistent reliability and unsafe behavior variance.

This plan makes runtime behavior deterministic and inspectable across models by combining:

1. explicit self-model contract (`wvp.1`)
2. deterministic model-tier registry (`wvp.2`)
3. adaptive autonomy gate using tier x calibration (`wvp.3`)

## Product intent from both perspectives

### Agent using Kura

- Always receives a machine-readable contract for what it can safely do.
- Can explain why autonomy was reduced (`reason_codes`), instead of guessing.
- Gets conservative fallback when identity or policy resolution is uncertain.

### Agent developing Kura

- Has one deterministic resolver path for identity -> tier -> gate decision.
- Can implement/test behavior via explicit matrix, not implicit heuristics.
- Can recover after context loss by reading this file + bead tasks.

## Scope (V1)

- Add `self_model` to `/v1/agent/context` and `/v1/agent/capabilities`.
- Resolve model identity deterministically from trusted server-side sources.
- Map identity to capability tier (`strict|moderate|advanced`) with conservative fallback.
- Merge tier policy with calibration status into write/workflow gating.
- Emit deterministic machine-readable reason codes in response/event payloads.

## Non-goals (V1)

- No external benchmark API calls in runtime decision path.
- No client-supplied model assertions that can elevate privileges.
- No hidden/ML policy routing; behavior must be rule-based and testable.

## Source-of-Truth: model identity resolution

`resolved_model_identity` MUST be computed server-side and deterministic.

Resolution order:

1. Access token client mapping (`auth.auth_method=AccessToken.client_id`) via
   `KURA_AGENT_MODEL_BY_CLIENT_ID_JSON` (JSON object map: `client_id -> model_identity`)
2. Runtime default identity via `KURA_AGENT_MODEL_IDENTITY`
3. Fallback: `"unknown"`

Rules:

- Unknown/malformed/missing identity MUST never increase autonomy.
- Unknown identity MUST map to `strict` tier.
- Add reason code `model_identity_unknown_fallback_strict` when fallback is used.

## Self-model contract (`wvp.1`)

Add `self_model` block with explicit schema version.

```json
{
  "schema_version": "agent_self_model.v1",
  "model_identity": "openai:gpt-5-mini",
  "capability_tier": "moderate",
  "known_limitations": ["..."],
  "preferred_contracts": {
    "read": "/v1/agent/context",
    "write": "/v1/agent/write-with-proof"
  },
  "fallback_behavior": {
    "unknown_identity_action": "fallback_strict",
    "unknown_policy_action": "deny"
  },
  "docs": {
    "runtime_policy": "system.conventions.model_tier_registry_v1",
    "upgrade_hint": "/v1/agent/capabilities"
  }
}
```

Compatibility:

- Conservative defaults for legacy/unknown clients.
- Unknown fields remain deny-by-default via contract metadata.

## Tier registry + policy mapping (`wvp.2`)

Add `model_tier_registry_v1` as machine-readable policy surface in conventions.

Policy shape:

- tiers: `strict|moderate|advanced`
- per-tier settings:
  - `confidence_floor`
  - `allowed_action_scope` (`strict|moderate|proactive`)
  - `high_impact_write_policy` (`allow|confirm_first|block`)
  - `repair_auto_apply_cap` (`enabled|confirm_only|disabled`)

Deterministic fallback:

- unresolved identity -> `strict`
- malformed identity -> `strict`

External benchmark usage:

- Allowed only for offline/default registry curation.
- Not allowed as online runtime gate input.

## Adaptive autonomy gate (`wvp.3`)

Gate input tuple:

- `model_tier`: strict|moderate|advanced
- `calibration_status`: healthy|monitor|degraded
- `integrity_slo_status`: healthy|monitor|degraded
- `action_class`: low_impact_write|high_impact_write

`effective_quality_status = worst(calibration_status, integrity_slo_status)`

Decision rules (deterministic):

1. If `effective_quality_status=degraded` and action is high-impact -> `block`
2. Else if tier policy says `high_impact_write_policy=block` and action is high-impact -> `block`
3. Else if `effective_quality_status=monitor` and action is high-impact -> `confirm_first`
4. Else if tier policy says `confirm_first` for this action class -> `confirm_first`
5. Else -> `allow`

Output contract:

```json
{
  "autonomy_gate": {
    "decision": "allow|confirm_first|block",
    "action_class": "low_impact_write|high_impact_write",
    "model_tier": "strict|moderate|advanced",
    "effective_quality_status": "healthy|monitor|degraded",
    "reason_codes": ["..."]
  }
}
```

## Reason-code catalog (V1 minimum)

- `model_identity_unknown_fallback_strict`
- `model_tier_strict_blocks_high_impact_write`
- `model_tier_requires_confirmation`
- `calibration_monitor_requires_confirmation`
- `integrity_monitor_requires_confirmation`
- `calibration_degraded_blocks_high_impact_write`
- `integrity_degraded_blocks_high_impact_write`
- `workflow_onboarding_gate_blocked` (existing workflow gate can be included in merged reasons)

Requirements:

- deterministic, stable strings
- attach to API response and quality signal event payload
- never return empty reason_codes for `confirm_first|block`

## High-impact write classification (V1)

High-impact if request contains any planning/coaching event type already recognized by
`workflow_gate_from_request` and/or explicit policy-labeled high-impact event types.

Conservative rule:

- if classification is ambiguous, classify as high-impact.

## Integration points (code)

- `api/src/routes/agent.rs`
  - add self-model structs and serialization in context/capabilities
  - add model identity resolver (trusted sources only)
  - add tier registry resolver + policy mapping
  - add autonomy gate evaluator in `write_with_proof`
  - extend response/event payload with `autonomy_gate` + `reason_codes`
- `workers/src/kura_workers/system_config.py`
  - add `model_tier_registry_v1` convention contract
- tests
  - `api/src/routes/agent.rs` unit tests for resolver and gate matrix
  - `workers/tests/test_system_config.py` contract visibility tests
  - `workers/tests/test_integration.py` key integration scenarios

## Test matrix (must-pass)

### wvp.1

- self_model present and versioned in context/capabilities
- conservative defaults for unknown/legacy resolution
- redaction + schema stability checks

### wvp.2

- known identity -> expected tier
- unknown identity -> strict fallback
- malformed identity -> strict fallback
- conventions expose machine-readable tier policy

### wvp.3

- each tier (`strict|moderate|advanced`) x quality (`healthy|monitor|degraded`)
- decisions match deterministic matrix
- reason_codes present and stable for confirm/block
- high-impact write blocks where expected

## Rollout and safety

- Default to conservative behavior if resolver errors.
- Add env-gated rollout switch for new gate behavior if needed.
- Keep hard safety invariants non-bypassable.
- Log decision tuple + reason_codes for audit.

## Implementation order (strict)

1. `wvp.1` self_model contract
2. `wvp.2` tier registry + mapping
3. `wvp.3` adaptive gate + reason codes
4. run quality gates
5. close tasks in dependency order

## Recovery after compaction

If conversation context is lost:

1. run `scripts/bd-safe.sh prime`
2. open this file
3. run `scripts/bd-safe.sh show wvp.1`, `wvp.2`, `wvp.3`
4. execute child tasks in dependency order
5. validate with test matrix above

This file and the bead dependency graph are the source of execution truth.
