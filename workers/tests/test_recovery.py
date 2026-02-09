"""Tests for recovery handler pure functions and data handling."""

from datetime import date

from kura_workers.handlers.recovery import (
    _iso_week,
    _manifest_contribution,
)


class TestIsoWeek:
    def test_normal_date(self):
        assert _iso_week(date(2026, 2, 8)) == "2026-W06"

    def test_year_boundary(self):
        assert _iso_week(date(2025, 12, 29)) == "2026-W01"


class TestManifestContribution:
    def test_full_recovery_data(self):
        rows = [{
            "key": "overview",
            "data": {
                "sleep": {
                    "overall": {
                        "avg_duration_hours": 7.3,
                        "total_entries": 45,
                    },
                },
                "soreness": {
                    "total_entries": 12,
                },
                "energy": {
                    "overall": {
                        "avg_level": 6.8,
                        "total_entries": 30,
                    },
                },
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "avg_sleep_hours": 7.3,
            "total_sleep_entries": 45,
            "total_soreness_entries": 12,
            "avg_energy_level": 6.8,
        }

    def test_sleep_only(self):
        rows = [{
            "key": "overview",
            "data": {
                "sleep": {
                    "overall": {
                        "avg_duration_hours": 8.0,
                        "total_entries": 10,
                    },
                },
                "soreness": {"total_entries": 0},
                "energy": {},
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {
            "avg_sleep_hours": 8.0,
            "total_sleep_entries": 10,
        }

    def test_empty_data(self):
        rows = [{
            "key": "overview",
            "data": {
                "sleep": {},
                "soreness": {"total_entries": 0},
                "energy": {},
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {}

    def test_empty_rows(self):
        assert _manifest_contribution([]) == {}

    def test_soreness_only(self):
        rows = [{
            "key": "overview",
            "data": {
                "sleep": {},
                "soreness": {"total_entries": 5},
                "energy": {},
            },
        }]
        result = _manifest_contribution(rows)
        assert result == {"total_soreness_entries": 5}

    def test_with_targets(self):
        rows = [{
            "key": "overview",
            "data": {
                "sleep": {"overall": {"avg_duration_hours": 7.0, "total_entries": 10}},
                "soreness": {"total_entries": 0},
                "energy": {},
                "targets": {"sleep": {"duration_hours": 8}},
            },
        }]
        result = _manifest_contribution(rows)
        assert result["has_targets"] is True
        assert result["avg_sleep_hours"] == 7.0
