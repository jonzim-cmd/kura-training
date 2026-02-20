from __future__ import annotations

from kura_workers.eval_harness import build_stratified_calibration_summary


def test_stratified_calibration_surface_requires_objective_modality_quality_segments() -> None:
    results = [
        {
            "projection_type": "readiness_inference",
            "status": "ok",
            "metrics": {"coverage_ci95_nowcast": 0.78},
            "objective_mode": "coach",
            "modality": "running",
            "quality_band": "high",
            "user_ref": "u1",
        },
        {
            "projection_type": "readiness_inference",
            "status": "insufficient_data",
            "metrics": {"coverage_ci95_nowcast": 0.42},
            "objective_mode": "coach",
            "modality": "running",
            "quality_band": "high",
            "user_ref": "u1",
        },
        {
            "projection_type": "causal_inference",
            "status": "ok",
            "metrics": {"ok_outcome_rate": 0.64},
            "objective_mode": "collaborate",
            "modality": "cycling",
            "quality_band": "medium",
            "user_ref": "u2",
        },
    ]
    summary = build_stratified_calibration_summary(results)
    assert summary["schema_version"] == "stratified_calibration.v1"
    assert summary["segmentation_axes"] == ["objective_mode", "modality", "quality_band"]
    assert summary["global"]["sample_size"] == 3
    assert len(summary["segments"]) >= 2
    for segment in summary["segments"]:
        assert "brier_score" in segment
        assert "ece" in segment
        assert "coverage_ci95" in segment
        assert "brier_score_shrunk" in segment
        assert "ece_shrunk" in segment


def test_stratified_calibration_marks_small_sample_segments_with_caveat() -> None:
    summary = build_stratified_calibration_summary(
        [
            {
                "projection_type": "semantic_memory",
                "status": "ok",
                "metrics": {"top1_accuracy": 0.91},
                "objective_mode": "coach",
                "modality": "rowing",
                "quality_band": "high",
                "user_ref": "u1",
            }
        ]
    )
    assert len(summary["segments"]) == 1
    assert summary["segments"][0]["small_sample_caveat"] is True
