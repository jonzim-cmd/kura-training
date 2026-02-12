# Learning-to-Backlog Bridge + Regression Promotion V1 (2zc.3)

Date: 2026-02-12  
Owner: Team (JZ + Codex)  
Issue: `kura-training-2zc.3`

## Goal

Turn recurring reliability learnings into actionable, machine-readable backlog
candidates with explicit promotion workflow (cluster -> issue -> invariant/policy/test
-> shadow re-evaluation).

## Inputs

Nightly sources:

- `learning_issue_clusters` (weekly clusters from `2zc.2`)
- `extraction_underperforming_classes` (weekly calibration alerts from `2zc.5`)

## Outputs

Tables:

- `learning_backlog_candidates`
- `learning_backlog_bridge_runs`

Each candidate stores:

- stable `candidate_key` (dedupe key)
- source metadata (`source_type`, `source_period_key`, `source_ref`)
- `priority_score` and `title`
- `root_cause_hypothesis`
- `impacted_metrics`
- `suggested_updates` (invariant/policy/regression-test hints)
- `promotion_checklist` (auto + manual steps)
- full machine-readable `issue_payload`
- `approval_required=true` (human gate, V1)

## Guardrails

Noise filters:

- cluster min score/support/user thresholds
- calibration min sample threshold
- per-source candidate cap
- per-run global candidate cap

Duplicate controls:

- stable candidate key hashing
- skip already approved/promoted candidates
- update existing `candidate`/`dismissed` rows only

## Promotion Checklist Automation

Auto-completed where possible:

- root-cause hypothesis attached
- invariant/policy mapping attached
- regression test plan attached

Manual gates remain explicit:

- human approval for issue creation
- regression test implementation
- shadow re-evaluation pass before rollout

## Nightly Integration

`inference.nightly_refit` now executes:

1. population prior refresh
2. issue clustering refresh
3. extraction calibration refresh
4. learning backlog bridge refresh

This keeps backlog candidates synchronized with latest weekly reliability signals.

## Non-Goals (V1)

- No automatic creation of `bd` issues.
- No automatic code/policy patching from candidate payloads.
- No rollout decisions from bridge alone (handled by `2zc.4` shadow evaluation).

## Rollback / Disable

- Remove `refresh_learning_backlog_candidates(...)` from nightly handler.
- Ignore `learning_backlog_candidates` in downstream promotion flow.
- Keep source pipelines (`2zc.2` / `2zc.5`) active independently.
