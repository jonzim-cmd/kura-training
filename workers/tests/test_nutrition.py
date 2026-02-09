"""Tests for nutrition handler pure functions and data handling."""

from datetime import date

from kura_workers.handlers.nutrition import (
    _iso_week,
    _manifest_contribution,
)


class TestIsoWeek:
    def test_normal_date(self):
        assert _iso_week(date(2026, 2, 8)) == "2026-W06"

    def test_year_boundary(self):
        assert _iso_week(date(2025, 12, 29)) == "2026-W01"


class TestManifestContribution:
    def test_full_nutrition_data(self):
        rows = [{
            "key": "overview",
            "data": {
                "total_meals": 120,
                "tracking_days": 45,
                "latest_date": "2026-02-08",
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "total_meals": 120,
            "tracking_days": 45,
            "latest_date": "2026-02-08",
        }

    def test_no_meals(self):
        rows = [{
            "key": "overview",
            "data": {
                "total_meals": 0,
                "tracking_days": 0,
                "latest_date": None,
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {}

    def test_empty_rows(self):
        assert _manifest_contribution([]) == {}

    def test_partial_data(self):
        rows = [{
            "key": "overview",
            "data": {
                "total_meals": 5,
                "tracking_days": 3,
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "total_meals": 5,
            "tracking_days": 3,
        }

    def test_with_target(self):
        rows = [{
            "key": "overview",
            "data": {
                "total_meals": 10,
                "tracking_days": 5,
                "latest_date": "2026-02-08",
                "target": {"calories": 2500, "protein_g": 180},
            },
        }]
        result = _manifest_contribution(rows)
        assert result["has_target"] is True
        assert result["total_meals"] == 10

    def test_without_target(self):
        rows = [{
            "key": "overview",
            "data": {
                "total_meals": 10,
                "tracking_days": 5,
                "latest_date": "2026-02-08",
            },
        }]
        result = _manifest_contribution(rows)
        assert "has_target" not in result
