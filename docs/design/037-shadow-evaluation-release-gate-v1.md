# Shadow Evaluation Release Gate V1 (2zc.4)

Date: 2026-02-12  
Owner: Team (JZ + Codex)  
Issue: `kura-training-2zc.4`

## Goal

Before rollout of policy or agent-behavior changes, run baseline and candidate
replay side-by-side and block rollout when key quality metrics regress beyond
defined tolerances.

## Harness

Entrypoints:

- `run_eval_harness(...)` for single-variant replay
- `run_shadow_evaluation(...)` for baseline-vs-candidate comparison

Shadow report includes:

- baseline summary + candidate summary
- metric deltas (`delta_abs`, `delta_pct`, sample counts, pass/fail per rule)
- failure classes (non-OK projection statuses + shadow gate failures)
- release gate verdict (`pass|fail|insufficient_data`)

## Gate Policy

Policy version: `shadow_eval_gate_v1`

Representative V1 rules:

- strength `coverage_ci95`: candidate drop max `-0.03`
- strength `mae`: candidate increase max `+1.0`
- readiness `coverage_ci95_nowcast`: candidate drop max `-0.03`
- readiness `mae_nowcast`: candidate increase max `+0.03`
- semantic/causal rules are applied when those projection types are selected

Rollout only allowed when:

- all required deltas are available
- all deltas pass thresholds
- candidate shadow mode status is `pass`

## Corpus and Privacy

`run_shadow_evaluation` accepts multiple `user_ids` and emits pseudonymized user
references in report output (`shadow_u_<hash>`). The report is corpus-oriented,
not tied to cleartext user identity.

## Example Report

Sample committed report:

- `docs/reports/shadow-eval-sample-report.json`

This file demonstrates expected report structure and gate semantics.

## Non-Goals (V1)

- No automatic deployment trigger.
- No automatic mitigation patching.
- No cross-environment orchestration; this layer only evaluates and gates.

## Rollback / Disable

- Skip `run_shadow_evaluation` in rollout workflow.
- Treat release-gate output as advisory only.
- Revert to single-run `run_eval_harness` checks for diagnostics-only mode.
