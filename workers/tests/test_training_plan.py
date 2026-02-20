"""Tests for training_plan handler pure functions and data handling."""

from datetime import datetime

from kura_workers.handlers.training_plan import _manifest_contribution
from kura_workers.handlers.training_plan import (
    _compute_rir_target_summary,
    _normalize_plan_sessions_with_rir,
    _resolve_optional_plan_name,
    _resolve_plan_name,
)


class TestManifestContribution:
    def test_active_plan(self):
        rows = [{
            "key": "overview",
            "data": {
                "active_plan": {
                    "plan_id": "plan_001",
                    "name": "5/3/1 BBB",
                    "sessions": [
                        {"name": "Squat Day", "exercises": []},
                        {"name": "Bench Day", "exercises": []},
                        {"name": "Deadlift Day", "exercises": []},
                        {"name": "OHP Day", "exercises": []},
                    ],
                },
                "total_plans": 3,
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "has_active_plan": True,
            "plan_name": "5/3/1 BBB",
            "sessions_per_week": 4,
            "total_plans": 3,
        }

    def test_no_active_plan(self):
        rows = [{
            "key": "overview",
            "data": {
                "active_plan": None,
                "total_plans": 2,
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "has_active_plan": False,
            "total_plans": 2,
        }

    def test_empty_rows(self):
        assert _manifest_contribution([]) == {}

    def test_plan_with_no_name(self):
        rows = [{
            "key": "overview",
            "data": {
                "active_plan": {
                    "plan_id": "p1",
                    "sessions": [{"name": "A"}, {"name": "B"}],
                },
                "total_plans": 1,
            },
        }]
        result = _manifest_contribution(rows)
        assert result["plan_name"] == "unnamed"
        assert result["sessions_per_week"] == 2

    def test_plan_with_empty_sessions(self):
        rows = [{
            "key": "overview",
            "data": {
                "active_plan": {
                    "plan_id": "p1",
                    "name": "Deload",
                    "sessions": [],
                },
                "total_plans": 1,
            },
        }]
        result = _manifest_contribution(rows)
        assert result["sessions_per_week"] == 0


class TestRirNormalization:
    def test_normalize_plan_sessions_preserves_explicit_target_rir(self):
        sessions = [
            {
                "name": "Lower A",
                "exercises": [
                    {"exercise_id": "barbell_back_squat", "target_rir": 2},
                ],
            }
        ]
        normalized = _normalize_plan_sessions_with_rir(sessions)
        exercise = normalized[0]["exercises"][0]
        assert exercise["target_rir"] == 2.0
        assert "target_rir_source" not in exercise

    def test_normalize_plan_sessions_infers_target_rir_from_target_rpe(self):
        sessions = [
            {
                "name": "Upper A",
                "exercises": [
                    {"exercise_id": "barbell_bench_press", "target_rpe": 8},
                ],
            }
        ]
        normalized = _normalize_plan_sessions_with_rir(sessions)
        exercise = normalized[0]["exercises"][0]
        assert exercise["target_rir"] == 2.0
        assert exercise["target_rir_source"] == "inferred_from_target_rpe"

    def test_compute_rir_target_summary(self):
        sessions = [
            {
                "name": "Upper A",
                "exercises": [
                    {"exercise_id": "barbell_bench_press", "target_rir": 2},
                    {
                        "exercise_id": "barbell_overhead_press",
                        "target_rir": 3,
                        "target_rir_source": "inferred_from_target_rpe",
                    },
                ],
            }
        ]
        summary = _compute_rir_target_summary(sessions)
        assert summary["exercises_total"] == 2
        assert summary["exercises_with_target_rir"] == 2
        assert summary["inferred_target_rir"] == 1
        assert summary["average_target_rir"] == 2.5


class TestPlanNaming:
    def test_resolve_plan_name_keeps_explicit_name(self):
        ts = datetime(2026, 2, 20, 12, 0, 0)
        assert (
            _resolve_plan_name("  Performance Block  ", plan_id="default", timestamp=ts)
            == "Performance Block"
        )

    def test_resolve_plan_name_generates_deterministic_fallback(self):
        ts = datetime(2026, 2, 20, 12, 0, 0)
        assert (
            _resolve_plan_name(None, plan_id="Pre-Pause-Feb-2026", timestamp=ts)
            == "plan-2026-02-20-pre-pause-feb-2026"
        )

    def test_resolve_optional_plan_name_ignores_blank_values(self):
        assert _resolve_optional_plan_name("   ") is None
        assert _resolve_optional_plan_name("Deload") == "Deload"
