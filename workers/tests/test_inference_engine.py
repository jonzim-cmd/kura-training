"""Tests for Bayesian inference utility functions."""

from kura_workers.inference_engine import (
    run_readiness_inference,
    run_strength_inference,
    weekly_phase_from_date,
)


def test_strength_inference_insufficient_data():
    result = run_strength_inference([(0.0, 100.0), (7.0, 102.0)])
    assert result["status"] == "insufficient_data"
    assert result["required_points"] == 3
    assert result["dynamics"]["samples"] == 2
    assert result["dynamics"]["trajectory_code"] in {
        "up_up",
        "up_flat",
        "up",
        "flat_up",
        "flat",
        "flat_down",
        "down_down",
        "down_flat",
        "down",
        "unknown",
    }


def test_strength_inference_returns_trend():
    points = [(0.0, 100.0), (7.0, 101.5), (14.0, 103.0), (21.0, 104.2), (28.0, 105.4)]
    result = run_strength_inference(points)

    assert result["engine"] in {"closed_form", "pymc"}
    assert "trend" in result
    assert "estimated_1rm" in result
    assert "predicted_1rm" in result
    assert isinstance(result["trend"]["plateau_probability"], float)
    assert "dynamics" in result
    assert result["dynamics"]["samples"] == len(points)
    assert "direction" in result["dynamics"]


def test_strength_inference_applies_population_prior():
    points = [(0.0, 100.0), (7.0, 101.0), (14.0, 102.0), (21.0, 103.0)]
    result = run_strength_inference(
        points,
        population_prior={
            "mean": 0.2,
            "var": 0.01,
            "blend_weight": 0.4,
            "cohort_key": "tm:strength|el:intermediate",
            "target_key": "bench_press",
            "participants_count": 30,
            "sample_size": 40,
            "computed_at": "2026-02-11T00:00:00Z",
        },
    )
    assert result["engine"] in {"closed_form", "pymc"}
    assert result["population_prior"]["applied"] is True
    assert result["population_prior"]["cohort_key"] == "tm:strength|el:intermediate"


def test_readiness_inference_insufficient_data():
    result = run_readiness_inference([0.6, 0.55, 0.62])
    assert result["status"] == "insufficient_data"
    assert result["required_points"] == 5
    assert result["dynamics"]["samples"] == 3


def test_readiness_inference_ok():
    observations = [0.58, 0.61, 0.64, 0.62, 0.66, 0.68, 0.63]
    result = run_readiness_inference(observations)

    assert result["status"] == "ok"
    assert result["engine"] == "normal_normal"
    assert "readiness_today" in result
    assert result["readiness_today"]["state"] in {"low", "moderate", "high"}
    assert "dynamics" in result
    assert result["dynamics"]["samples"] == len(observations)
    assert result["dynamics"]["state"] == result["readiness_today"]["state"]


def test_readiness_inference_applies_population_prior():
    observations = [0.51, 0.53, 0.55, 0.57, 0.58, 0.6, 0.61]
    result = run_readiness_inference(
        observations,
        population_prior={
            "mean": 0.7,
            "var": 0.02,
            "blend_weight": 0.5,
            "cohort_key": "tm:strength|el:intermediate",
            "target_key": "overview",
            "participants_count": 40,
            "sample_size": 50,
            "computed_at": "2026-02-11T00:00:00Z",
        },
    )
    assert result["status"] == "ok"
    assert result["population_prior"]["applied"] is True
    assert result["population_prior"]["target_key"] == "overview"


def test_readiness_inference_supports_day_offsets_and_observation_variances():
    observations = [0.52, 0.55, 0.57, 0.56, 0.6, 0.58]
    day_offsets = [0.0, 1.0, 3.0, 4.0, 7.0, 8.0]
    variances = [0.012, 0.013, 0.02, 0.019, 0.015, 0.014]

    result = run_readiness_inference(
        observations,
        day_offsets=day_offsets,
        observation_variances=variances,
    )

    assert result["status"] == "ok"
    diagnostics = result["diagnostics"]
    assert diagnostics["weighted_observations"] is True
    assert diagnostics["effective_observations"] > 0
    assert diagnostics["day_span_days"] == 8.0


def test_weekly_phase_from_date():
    phase = weekly_phase_from_date("2026-02-02")
    assert phase["day_of_week"] == "monday"
    assert phase["phase"] == "week_start"
    assert isinstance(phase["angle_deg"], float)
