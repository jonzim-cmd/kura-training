"""Tests for body_composition handler pure functions and data handling."""

from datetime import date

from kura_workers.handlers.body_composition import (
    _iso_week,
    _manifest_contribution,
)


class TestIsoWeek:
    def test_normal_date(self):
        assert _iso_week(date(2026, 2, 8)) == "2026-W06"

    def test_year_boundary(self):
        assert _iso_week(date(2025, 12, 29)) == "2026-W01"

    def test_week_01(self):
        assert _iso_week(date(2026, 1, 5)) == "2026-W02"


class TestManifestContribution:
    def test_with_weight_data(self):
        rows = [{
            "key": "overview",
            "data": {
                "current_weight_kg": 82.5,
                "total_weigh_ins": 30,
                "measurement_types": ["waist", "chest"],
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "current_weight_kg": 82.5,
            "total_weigh_ins": 30,
            "measurement_types": ["waist", "chest"],
        }

    def test_weight_only(self):
        rows = [{
            "key": "overview",
            "data": {
                "current_weight_kg": 80.0,
                "total_weigh_ins": 5,
                "measurement_types": [],
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "current_weight_kg": 80.0,
            "total_weigh_ins": 5,
        }

    def test_no_weight(self):
        rows = [{
            "key": "overview",
            "data": {
                "current_weight_kg": None,
                "total_weigh_ins": 0,
                "measurement_types": ["waist"],
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {"measurement_types": ["waist"]}

    def test_empty_rows(self):
        assert _manifest_contribution([]) == {}

    def test_zero_weigh_ins_excluded(self):
        rows = [{
            "key": "overview",
            "data": {
                "current_weight_kg": None,
                "total_weigh_ins": 0,
                "measurement_types": [],
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {}

    def test_with_target(self):
        rows = [{
            "key": "overview",
            "data": {
                "current_weight_kg": 85.0,
                "total_weigh_ins": 20,
                "measurement_types": [],
                "target": {"weight_kg": 80, "timeframe": "3 months"},
            },
        }]
        result = _manifest_contribution(rows)
        assert result["has_target"] is True
        assert result["current_weight_kg"] == 85.0

    def test_without_target(self):
        rows = [{
            "key": "overview",
            "data": {
                "current_weight_kg": 85.0,
                "total_weigh_ins": 20,
                "measurement_types": [],
            },
        }]
        result = _manifest_contribution(rows)
        assert "has_target" not in result
