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


def test_weekly_phase_from_date():
    phase = weekly_phase_from_date("2026-02-02")
    assert phase["day_of_week"] == "monday"
    assert phase["phase"] == "week_start"
    assert isinstance(phase["angle_deg"], float)
