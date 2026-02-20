from __future__ import annotations

from kura_workers.eval_harness import build_statistical_robustness_guard


def test_statistical_robustness_guard_contract_flags_drift_failures() -> None:
    guard = build_statistical_robustness_guard(
        [],
        stratified_calibration={
            "segments": [
                {
                    "objective_mode": "coach",
                    "modality": "running",
                    "quality_band": "high",
                    "sample_size": 18,
                    "unique_users": 12,
                    "small_sample_caveat": False,
                }
            ]
        },
        uncertainty_calibration_drift={"status": "fail"},
    )
    assert guard["schema_version"] == "statistical_robustness_guard.v1"
    assert guard["policy_role"] == "advisory_regression_gate"
    assert guard["status"] == "fail"
    assert "calibration_drift_threshold_exceeded" in guard["reasons"]


def test_statistical_robustness_guard_contract_pins_small_sample_policy() -> None:
    guard = build_statistical_robustness_guard(
        [],
        stratified_calibration={
            "segments": [
                {
                    "objective_mode": "coach",
                    "modality": "running",
                    "quality_band": "high",
                    "sample_size": 2,
                    "unique_users": 1,
                    "small_sample_caveat": True,
                }
            ]
        },
        uncertainty_calibration_drift={"status": "pass"},
    )
    policy = guard["small_sample_policy"]
    assert policy["strategy"] == "hierarchical_shrinkage"
    assert policy["must_emit_small_n_caveat"] is True
