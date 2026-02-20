from __future__ import annotations

from kura_workers.eval_harness import build_uncertainty_calibration_drift_guard


def test_uncertainty_calibration_drift_guard_flags_large_segment_drift() -> None:
    guard = build_uncertainty_calibration_drift_guard(
        {
            "global": {"ece": 0.04, "brier_score": 0.08},
            "segments": [
                {
                    "objective_mode": "coach",
                    "modality": "running",
                    "quality_band": "high",
                    "sample_size": 25,
                    "ece_shrunk": 0.25,
                    "brier_score_shrunk": 0.22,
                }
            ],
        }
    )
    assert guard["schema_version"] == "uncertainty_calibration_drift.v1"
    assert guard["status"] == "fail"
    assert guard["drifted_segments"]
    assert guard["thresholds"]["max_abs_ece_delta"] > 0
    assert guard["thresholds"]["max_abs_brier_delta"] > 0


def test_uncertainty_calibration_drift_guard_passes_when_segments_stable() -> None:
    guard = build_uncertainty_calibration_drift_guard(
        {
            "global": {"ece": 0.08, "brier_score": 0.12},
            "segments": [
                {
                    "objective_mode": "coach",
                    "modality": "running",
                    "quality_band": "high",
                    "sample_size": 25,
                    "ece_shrunk": 0.09,
                    "brier_score_shrunk": 0.13,
                }
            ],
        }
    )
    assert guard["status"] == "pass"
    assert guard["drifted_segments"] == []
