from __future__ import annotations

from kura_workers.system_config import _get_conventions
from kura_workers.training_load_calibration_v1 import (
    BASELINE_PARAMETER_VERSION,
    CALIBRATED_PARAMETER_VERSION,
    build_calibration_runner_report,
    calibration_protocol_v1,
    compare_versions_shadow,
)


def _samples(count: int = 24) -> list[dict]:
    return [
        {
            "data": {
                "duration_seconds": 1800,
                "distance_meters": 5000,
                "heart_rate_avg": 160,
                "power_watt": 280,
                "pace_min_per_km": 3.6,
            },
            "source_type": "external_import",
            "session_confidence_hint": 0.9,
            "expected_confidence": 0.9,
            "expected_tier": "analysis_advanced",
        }
        for _ in range(count)
    ]


def test_training_load_calibration_contract_is_declared() -> None:
    conventions = _get_conventions()
    contract = conventions["training_load_calibration_v1"]["contract"]
    assert contract["schema_version"] == "training_load_calibration.v1"
    assert {
        "brier_score",
        "mae",
        "calibration_error",
        "ranking_consistency",
    } <= set(contract["metrics"])


def test_training_load_calibration_shadow_report_is_structured_and_guarded() -> None:
    shadow = compare_versions_shadow(
        _samples(),
        baseline_version=BASELINE_PARAMETER_VERSION,
        candidate_version=CALIBRATED_PARAMETER_VERSION,
    )
    assert shadow["schema_version"] == "training_load_calibration_shadow.v1"
    assert shadow["guardrails"]["checks"]
    assert shadow["allow_rollout"] in {True, False}


def test_training_load_calibration_runner_returns_recommended_version() -> None:
    report = build_calibration_runner_report(_samples())
    assert report["schema_version"] == "training_load_calibration_runner.v1"
    assert report["recommended_version"] in {
        BASELINE_PARAMETER_VERSION,
        CALIBRATED_PARAMETER_VERSION,
    }
    protocol = calibration_protocol_v1()
    assert report["recommended_version"] in protocol["parameter_registry"]["available_versions"]
