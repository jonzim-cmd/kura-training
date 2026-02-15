# Advisory Scoring v1 Runbook

## Purpose
Operate and tune `advisory_scoring_layer.v1` in a low-friction, advisory-only mode for `POST /v1/agent/write-with-proof`.

## Scope
- In scope: score calculation, action mapping, telemetry monitoring, threshold tuning.
- Out of scope: hard-block autonomy decisions from advisory scoring.

## Contract Surface
- Response fields:
  - `advisory_scores`
  - `advisory_action_plan`
- Learning signal:
  - `learning.signal.logged` with `signal_type=advisory_scoring_assessed`
- Conventions:
  - `advisory_scoring_layer_v1` in system config.

## Rollout Plan
1. Shadow read:
   - Compute and emit `advisory_scores` + signal events.
   - Do not change existing autonomy gate logic.
2. Advisory activation:
   - Use `advisory_action_plan` to nudge response wording and persistence advice.
   - Keep `policy_role=advisory_only`.
3. Stabilization:
   - Watch anomaly rates for high hallucination/data-quality risk.
   - Tune thresholds only with reproducible test updates.

## Monitoring
Use admin telemetry endpoints:
- `/v1/admin/agent/telemetry/overview`
- `/v1/admin/agent/telemetry/anomalies`
- `/v1/admin/agent/telemetry/signals?signal_type=advisory_scoring_assessed`

Primary metrics:
- `advisory_high_hallucination_risk_rate_pct`
- `advisory_high_data_quality_risk_rate_pct`
- `advisory_high_risk_cautious_rate_pct`
- `advisory_high_risk_persist_now_rate_pct`
- `retrieval_regret_rate_pct`
- `context_read_coverage_pct`

## Alert Guidance
- Warning if hallucination high-risk rate >= 35% with at least 10 advisory samples.
- Warning if data-quality high-risk rate >= 35% with at least 10 advisory samples.
- Warning if `advisory_high_risk_persist_now_rate_pct` >= 25% with at least 10 high-risk runs.
- Critical if write-with-proof error rate >= 8% with enough volume.

## Safe Tuning Rules
- Never introduce `block` as advisory action.
- Keep clarification budget max at 1 per risky step.
- Reconcile with `persist_intent`:
  - `ask_first` in persist intent must not be downgraded.
- Keep saved wording proof-bound:
  - no saved confirmation phrasing when claim is not verified.

## Rollback
If regressions appear:
1. Keep score emission for diagnostics.
2. Disable advisory action usage in client orchestration.
3. Preserve existing `response_mode_policy`, `sidecar_assessment`, `persist_intent` behavior.

## Validation Checklist
- Architecture contracts green, including advisory scoring contract.
- Rust tests green for `kura-api`.
- Python worker lint/tests green.
- Anomaly profile stable across at least one monitoring window.
