from __future__ import annotations

from kura_workers.eval_harness import (
    build_statistical_robustness_guard,
    build_stratified_calibration_summary,
    build_uncertainty_calibration_drift_guard,
)


def _eval_row(*, objective_mode: str, modality: str, quality_band: str, confidence: float) -> dict:
    return {
        "projection_type": "readiness_inference",
        "status": "ok",
        "metrics": {"coverage_ci95_nowcast": confidence},
        "objective_mode": objective_mode,
        "modality": modality,
        "quality_band": quality_band,
        "user_ref": f"{objective_mode}-{modality}",
    }


def test_cross_objective_eval_gate_surfaces_per_mode_segments() -> None:
    results = [
        _eval_row(
            objective_mode="coach",
            modality="running",
            quality_band="high",
            confidence=0.74,
        ),
        _eval_row(
            objective_mode="collaborate",
            modality="running",
            quality_band="high",
            confidence=0.71,
        ),
    ]
    summary = build_stratified_calibration_summary(results)
    assert {"coach", "collaborate"} <= set(summary["by_axis"]["objective_mode"])
    assert {
        segment["objective_mode"] for segment in summary["segments"]
    } == {"coach", "collaborate"}


def test_cross_objective_eval_gate_fails_when_single_objective_mode_drifts() -> None:
    stratified = {
        "global": {"ece": 0.05, "brier_score": 0.04},
        "segments": [
            {
                "objective_mode": "coach",
                "modality": "running",
                "quality_band": "high",
                "sample_size": 32,
                "ece_shrunk": 0.26,
                "brier_score_shrunk": 0.18,
                "small_sample_caveat": False,
                "unique_users": 18,
            },
            {
                "objective_mode": "collaborate",
                "modality": "running",
                "quality_band": "high",
                "sample_size": 30,
                "ece_shrunk": 0.07,
                "brier_score_shrunk": 0.06,
                "small_sample_caveat": False,
                "unique_users": 17,
            },
        ],
    }
    drift = build_uncertainty_calibration_drift_guard(stratified)
    guard = build_statistical_robustness_guard(
        [],
        stratified_calibration=stratified,
        uncertainty_calibration_drift=drift,
    )
    assert drift["status"] == "fail"
    assert any(segment["objective_mode"] == "coach" for segment in drift["drifted_segments"])
    assert guard["status"] == "fail"
