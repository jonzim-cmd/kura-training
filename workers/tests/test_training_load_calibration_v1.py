from __future__ import annotations

from datetime import UTC, datetime

from kura_workers.training_load_calibration_v1 import (
    BASELINE_PARAMETER_VERSION,
    CALIBRATED_PARAMETER_VERSION,
    FEATURE_FLAG_TRAINING_LOAD_CALIBRATED,
    FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY,
    active_calibration_version,
    build_calibration_runner_report,
    compute_row_load_components_v2,
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


def test_relative_intensity_signal_is_used_when_reference_is_fresh() -> None:
    profile = calibration_profile_for_version(CALIBRATED_PARAMETER_VERSION)
    now_iso = datetime.now(tz=UTC).isoformat()
    with_relative = compute_row_load_components_v2(
        data={
            "duration_seconds": 600,
            "distance_meters": 2000,
            "rpe": 5,
            "relative_intensity": {
                "value_pct": 85.0,
                "reference_type": "e1rm",
                "reference_value": 120.0,
                "reference_measured_at": now_iso,
                "reference_confidence": 0.8,
            },
        },
        profile=profile,
    )
    fallback = compute_row_load_components_v2(
        data={
            "duration_seconds": 600,
            "distance_meters": 2000,
            "rpe": 5,
        },
        profile=profile,
    )
    assert str(with_relative["internal_response_source"]).startswith("relative_intensity:e1rm")
    assert with_relative["relative_intensity_status"] == "used"
    assert float(with_relative["load_score"]) > float(fallback["load_score"])


def test_stale_relative_intensity_references_fallback_with_uncertainty_uplift() -> None:
    profile = calibration_profile_for_version(CALIBRATED_PARAMETER_VERSION)
    stale = compute_row_load_components_v2(
        data={
            "duration_seconds": 900,
            "distance_meters": 3000,
            "rpe": 8,
            "relative_intensity": {
                "value_pct": 90.0,
                "reference_type": "critical_speed",
                "reference_value": 4.4,
                "reference_measured_at": "2020-01-01T00:00:00+00:00",
                "reference_confidence": 0.9,
            },
        },
        profile=profile,
    )
    baseline = compute_row_load_components_v2(
        data={
            "duration_seconds": 900,
            "distance_meters": 3000,
            "rpe": 8,
        },
        profile=profile,
    )
    assert stale["relative_intensity_status"] == "fallback_stale_reference"
    assert "relative_fallback:stale_reference" in str(stale["internal_response_source"])
    assert float(stale["uncertainty"]) > float(baseline["uncertainty"])


def test_missing_relative_reference_metadata_falls_back_deterministically() -> None:
    profile = calibration_profile_for_version(CALIBRATED_PARAMETER_VERSION)
    missing_reference = compute_row_load_components_v2(
        data={
            "duration_seconds": 900,
            "distance_meters": 3000,
            "rpe": 7,
            "relative_intensity": {
                "value_pct": 88.0,
            },
        },
        profile=profile,
    )
    baseline = compute_row_load_components_v2(
        data={
            "duration_seconds": 900,
            "distance_meters": 3000,
            "rpe": 7,
        },
        profile=profile,
    )
    assert missing_reference["relative_intensity_status"] == "fallback_missing_reference"
    assert "relative_fallback:missing_reference" in str(
        missing_reference["internal_response_source"]
    )
    assert float(missing_reference["uncertainty"]) > float(baseline["uncertainty"])


def test_relative_intensity_can_be_disabled_by_feature_flag(monkeypatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY, "false")
    profile = calibration_profile_for_version(CALIBRATED_PARAMETER_VERSION)
    components = compute_row_load_components_v2(
        data={
            "duration_seconds": 900,
            "distance_meters": 3000,
            "rpe": 7,
            "relative_intensity": {
                "value_pct": 95.0,
                "reference_type": "critical_speed",
                "reference_value": 4.4,
                "reference_measured_at": datetime.now(tz=UTC).isoformat(),
                "reference_confidence": 0.9,
            },
        },
        profile=profile,
    )
    assert components["relative_intensity_status"] == "disabled"
    assert not str(components["internal_response_source"]).startswith("relative_intensity")
