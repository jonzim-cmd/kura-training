from __future__ import annotations

from kura_workers.eval_harness import _simpson_sign_reversal_indicators


def test_simpson_paradox_detection_flags_sign_reversal_between_global_and_segment_deltas() -> None:
    results: list[dict] = []

    # Segment A: event_store > projection_history (positive delta), but source counts are skewed.
    for _ in range(5):
        results.append(
            {
                "projection_type": "strength_inference",
                "source": "projection_history",
                "status": "ok",
                "metrics": {"coverage_ci95": 0.10},
                "objective_mode": "coach",
                "modality": "running",
                "quality_band": "high",
            }
        )
    results.append(
        {
            "projection_type": "strength_inference",
            "source": "event_store",
            "status": "ok",
            "metrics": {"coverage_ci95": 0.90},
            "objective_mode": "coach",
            "modality": "running",
            "quality_band": "high",
        }
    )

    # Segment B: event_store < projection_history (negative delta), with opposite source skew.
    results.append(
        {
            "projection_type": "strength_inference",
            "source": "projection_history",
            "status": "ok",
            "metrics": {"coverage_ci95": 0.90},
            "objective_mode": "coach",
            "modality": "cycling",
            "quality_band": "high",
        }
    )
    for _ in range(9):
        results.append(
            {
                "projection_type": "strength_inference",
                "source": "event_store",
                "status": "ok",
                "metrics": {"coverage_ci95": 0.20},
                "objective_mode": "coach",
                "modality": "cycling",
                "quality_band": "high",
            }
        )

    indicators = _simpson_sign_reversal_indicators(results)
    strength_coverage_reversal = [
        item
        for item in indicators
        if item["projection_type"] == "strength_inference"
        and item["metric"] == "coverage_ci95"
    ]
    assert strength_coverage_reversal
    assert strength_coverage_reversal[0]["segments_compared"] >= 2
