"""Tests for training_plan handler pure functions and data handling."""

from kura_workers.handlers.training_plan import _manifest_contribution


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
