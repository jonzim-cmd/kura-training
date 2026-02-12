# Unknown-Dimension Mining + Suggestion Loop V1 (2zc.6)

Date: 2026-02-12  
Owner: Team (JZ + Codex)  
Issue: `kura-training-2zc.6`

## Goal

Continuously discover recurring unknown/provisional observations and convert them
into ranked, auditable dimension-contract proposals.

## Inputs

Source stream:

- `observation.logged` events (unknown + provisional tiers only)
- retractions are respected (`event.retracted` filters)

## Mining Pipeline

1. Normalize unknown/provisional observations (dimension, scope, unit, value type, context).
2. Build deterministic semantic clusters by `scope_level + semantic_fingerprint`.
3. Apply noise controls (minimum support + minimum unique users).
4. Derive suggested schema (`name`, `value_type`, `expected_unit`, `expected_scale`).
5. Compute proposal score + confidence and attach risk notes.
6. Persist ranked proposals with evidence bundles.

## Outputs

Tables:

- `unknown_dimension_proposals`
- `unknown_dimension_mining_runs`

Proposal payload includes:

- `suggested_dimension`
- `confidence` + `proposal_score`
- `evidence_bundle` (sample utterances, representative examples, distributions)
- `risk_notes`
- approval workflow metadata

## Approval + Promotion

Status lifecycle:

- `candidate` -> `accepted` -> `promoted`
- `dismissed` for rejected hypotheses

V1 keeps explicit human approval before promotion.

## Backlog Bridge Integration

Accepted proposals are routed into `learning_backlog_candidates` via
`source_type=unknown_dimension` with stable dedupe keys.

This prevents noisy duplicate tickets and keeps a single canonical promotion path.

## Non-Goals (V1)

- No automatic schema mutation of `open_observations` registry.
- No auto-creation of tracker issues.
- No fully semantic/embedding clustering (deterministic lexical fingerprinting only).

## Rollback / Disable

- Remove `refresh_unknown_dimension_proposals(...)` from nightly handler.
- Keep unknown proposals table read-only (no routing to backlog bridge).
- Revert backlog bridge source type expansion for `unknown_dimension` if needed.
