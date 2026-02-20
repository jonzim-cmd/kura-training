from __future__ import annotations

from kura_workers.eval_harness import (
    build_stratified_calibration_summary,
    build_uncertainty_calibration_drift_guard,
)


def _eval_row(*, modality: str, confidence: float, user_ref: str) -> dict:
    return {
        "projection_type": "readiness_inference",
        "status": "ok",
        "metrics": {"coverage_ci95_nowcast": confidence},
        "objective_mode": "coach",
        "modality": modality,
        "quality_band": "high",
        "user_ref": user_ref,
    }


def test_major_modality_eval_gate_surfaces_running_cycling_rowing_swimming() -> None:
    summary = build_stratified_calibration_summary(
        [
            _eval_row(modality="running", confidence=0.74, user_ref="u1"),
            _eval_row(modality="cycling", confidence=0.71, user_ref="u2"),
            _eval_row(modality="rowing", confidence=0.69, user_ref="u3"),
            _eval_row(modality="swimming", confidence=0.68, user_ref="u4"),
        ]
    )
    modality_axis = set(summary["by_axis"]["modality"])
    assert {"running", "cycling", "rowing", "swimming"} <= modality_axis
    assert {
        segment["modality"] for segment in summary["segments"]
    } == {"running", "cycling", "rowing", "swimming"}


def test_major_modality_eval_gate_flags_single_modality_drift() -> None:
    drift = build_uncertainty_calibration_drift_guard(
        {
            "global": {"ece": 0.05, "brier_score": 0.05},
            "segments": [
                {
                    "objective_mode": "coach",
                    "modality": "running",
                    "quality_band": "high",
                    "sample_size": 24,
                    "ece_shrunk": 0.06,
                    "brier_score_shrunk": 0.05,
                },
                {
                    "objective_mode": "coach",
                    "modality": "cycling",
                    "quality_band": "high",
                    "sample_size": 24,
                    "ece_shrunk": 0.07,
                    "brier_score_shrunk": 0.05,
                },
                {
                    "objective_mode": "coach",
                    "modality": "rowing",
                    "quality_band": "high",
                    "sample_size": 24,
                    "ece_shrunk": 0.07,
                    "brier_score_shrunk": 0.06,
                },
                {
                    "objective_mode": "coach",
                    "modality": "swimming",
                    "quality_band": "high",
                    "sample_size": 24,
                    "ece_shrunk": 0.29,
                    "brier_score_shrunk": 0.17,
                },
            ],
        }
    )
    assert drift["status"] == "fail"
    assert any(segment["modality"] == "swimming" for segment in drift["drifted_segments"])
