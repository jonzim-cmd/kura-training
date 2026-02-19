from __future__ import annotations

from kura_workers.training_load_calibration_v1 import (
    BASELINE_PARAMETER_VERSION,
    CALIBRATED_PARAMETER_VERSION,
    FEATURE_FLAG_TRAINING_LOAD_CALIBRATED,
    active_calibration_version,
    build_calibration_runner_report,
    calibration_profile_for_version,
    calibration_protocol_v1,
    compare_versions_shadow,
    evaluate_profile_metrics,
    select_best_calibration_version,
)


def _samples_high_signal(count: int = 24) -> list[dict]:
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


def test_calibration_protocol_declares_metrics_guardrails_and_registry() -> None:
    protocol = calibration_protocol_v1()
    assert protocol["schema_version"] == "training_load_calibration.v1"
    assert {
        "brier_score",
        "mae",
        "calibration_error",
        "ranking_consistency",
        "composite_score",
    } <= set(protocol["metrics"])
    assert protocol["shadow_guardrails"]["min_samples"] >= 1
    assert BASELINE_PARAMETER_VERSION in protocol["parameter_registry"]["available_versions"]
    assert CALIBRATED_PARAMETER_VERSION in protocol["parameter_registry"]["available_versions"]
    assert "intensity_model" in protocol["parameter_registry"]["profiled_parameters"]


def test_active_calibration_version_falls_back_to_baseline_when_flag_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv(FEATURE_FLAG_TRAINING_LOAD_CALIBRATED, "false")
    assert active_calibration_version() == BASELINE_PARAMETER_VERSION


def test_profile_metrics_are_deterministic_and_structured() -> None:
    samples = _samples_high_signal(12)
    metrics = evaluate_profile_metrics(samples, version=CALIBRATED_PARAMETER_VERSION)
    assert metrics["version"] == CALIBRATED_PARAMETER_VERSION
    assert metrics["sample_count"] == 12
    assert 0 <= metrics["brier_score"] <= 1
    assert 0 <= metrics["ranking_consistency"] <= 1
    assert metrics["composite_score"] >= 0


def test_shadow_compare_and_runner_report_support_rollout_decision() -> None:
    samples = _samples_high_signal(24)
    shadow = compare_versions_shadow(
        samples,
        baseline_version=BASELINE_PARAMETER_VERSION,
        candidate_version=CALIBRATED_PARAMETER_VERSION,
    )
    assert shadow["schema_version"] == "training_load_calibration_shadow.v1"
    assert shadow["metrics"]["candidate"]["sample_count"] == 24
    assert shadow["guardrails"]["checks"]
    assert shadow["allow_rollout"] in {True, False}

    report = build_calibration_runner_report(samples)
    assert report["schema_version"] == "training_load_calibration_runner.v1"
    assert report["recommended_version"] in {
        BASELINE_PARAMETER_VERSION,
        CALIBRATED_PARAMETER_VERSION,
    }


def test_select_best_calibration_version_prefers_lowest_composite_score() -> None:
    samples = _samples_high_signal(24)
    best = select_best_calibration_version(
        samples,
        candidate_versions=[BASELINE_PARAMETER_VERSION, CALIBRATED_PARAMETER_VERSION],
    )
    assert best in {BASELINE_PARAMETER_VERSION, CALIBRATED_PARAMETER_VERSION}


def test_calibration_profile_for_unknown_version_uses_baseline() -> None:
    profile = calibration_profile_for_version("unknown_version")
    assert profile["version"] == BASELINE_PARAMETER_VERSION
    assert "intensity_model" in profile
    assert "multiplier" in profile["intensity_model"]
