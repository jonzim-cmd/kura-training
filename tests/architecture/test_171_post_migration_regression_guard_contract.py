from __future__ import annotations

from kura_workers.eval_harness import build_shadow_evaluation_report


def _shadow_eval(*, robustness_status: str) -> dict:
    return {
        "model_tier": "moderate",
        "source": "both",
        "strength_engine": "closed_form",
        "eval_status": "partial",
        "shadow_mode": {"status": "pass"},
        "stratified_calibration": {"segments": []},
        "uncertainty_calibration_drift": {"status": "pass"},
        "statistical_robustness_guard": {"status": robustness_status},
        "summary": {},
        "summary_by_source": {},
        "results": [],
    }


def test_release_gate_fails_when_candidate_robustness_guard_fails() -> None:
    report = build_shadow_evaluation_report(
        baseline_eval=_shadow_eval(robustness_status="pass"),
        candidate_eval=_shadow_eval(robustness_status="fail"),
    )
    gate = report["release_gate"]
    assert gate["status"] == "fail"
    assert "statistical_robustness_guard" in gate["failed_metrics"]
    assert any(
        reason.startswith("candidate_statistical_robustness_status=fail")
        for reason in gate["reasons"]
    )


def test_release_gate_marks_insufficient_data_when_candidate_robustness_lacks_coverage() -> None:
    report = build_shadow_evaluation_report(
        baseline_eval=_shadow_eval(robustness_status="pass"),
        candidate_eval=_shadow_eval(robustness_status="insufficient_data"),
    )
    gate = report["release_gate"]
    assert gate["status"] == "insufficient_data"
    assert any(
        reason.startswith("candidate_statistical_robustness_status=insufficient_data")
        for reason in gate["reasons"]
    )
