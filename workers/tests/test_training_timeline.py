"""Tests for training_timeline handler pure functions."""

from datetime import date

from kura_workers.handlers.training_timeline import (
    _compute_frequency,
    _compute_recent_days,
    _compute_streak,
    _compute_weekly_summary,
    _iso_week,
)


class TestIsoWeek:
    def test_normal_week(self):
        assert _iso_week(date(2026, 2, 8)) == "2026-W06"

    def test_first_week(self):
        # 2026-01-01 is a Thursday → ISO week 1
        assert _iso_week(date(2026, 1, 1)) == "2026-W01"

    def test_year_boundary(self):
        # 2025-12-29 is a Monday → ISO week 1 of 2026
        assert _iso_week(date(2025, 12, 29)) == "2026-W01"


class TestComputeRecentDays:
    def test_single_day(self):
        day_data = {
            date(2026, 2, 8): {
                "exercises": {"squat", "bench"},
                "total_sets": 6,
                "total_volume_kg": 2400.0,
                "total_reps": 30,
            }
        }
        result = _compute_recent_days(day_data)
        assert len(result) == 1
        assert result[0]["date"] == "2026-02-08"
        assert result[0]["exercises"] == ["bench", "squat"]
        assert result[0]["total_sets"] == 6
        assert result[0]["total_volume_kg"] == 2400.0
        assert result[0]["total_reps"] == 30

    def test_chronological_order(self):
        day_data = {
            date(2026, 2, 6): {"exercises": {"squat"}, "total_sets": 3, "total_volume_kg": 1200.0, "total_reps": 15},
            date(2026, 2, 8): {"exercises": {"bench"}, "total_sets": 4, "total_volume_kg": 800.0, "total_reps": 20},
            date(2026, 2, 7): {"exercises": {"deadlift"}, "total_sets": 2, "total_volume_kg": 600.0, "total_reps": 10},
        }
        result = _compute_recent_days(day_data)
        dates = [r["date"] for r in result]
        assert dates == ["2026-02-06", "2026-02-07", "2026-02-08"]

    def test_max_30_days(self):
        from datetime import timedelta
        base = date(2026, 1, 1)
        day_data = {
            base + timedelta(days=i): {"exercises": {"squat"}, "total_sets": 1, "total_volume_kg": 100.0, "total_reps": 5}
            for i in range(35)
        }
        result = _compute_recent_days(day_data)
        assert len(result) == 30
        # Should keep most recent 30
        assert result[0]["date"] == "2026-01-06"
        assert result[-1]["date"] == "2026-02-04"

    def test_empty(self):
        assert _compute_recent_days({}) == []

    def test_volume_rounding(self):
        day_data = {
            date(2026, 2, 8): {
                "exercises": {"squat"},
                "total_sets": 1,
                "total_volume_kg": 123.456789,
                "total_reps": 5,
            }
        }
        result = _compute_recent_days(day_data)
        assert result[0]["total_volume_kg"] == 123.5


class TestComputeWeeklySummary:
    def test_single_week(self):
        week_data = {
            "2026-W06": {
                "training_days": 3,
                "total_sets": 15,
                "total_volume_kg": 5400.0,
                "exercises": {"squat", "bench"},
            }
        }
        result = _compute_weekly_summary(week_data)
        assert len(result) == 1
        assert result[0]["week"] == "2026-W06"
        assert result[0]["training_days"] == 3
        assert result[0]["total_sets"] == 15
        assert result[0]["exercises"] == ["bench", "squat"]

    def test_max_26_weeks(self):
        week_data = {
            f"2026-W{i+1:02d}": {
                "training_days": 2,
                "total_sets": 10,
                "total_volume_kg": 3000.0,
                "exercises": {"squat"},
            }
            for i in range(30)
        }
        result = _compute_weekly_summary(week_data)
        assert len(result) == 26
        # Should keep most recent 26 weeks
        assert result[0]["week"] == "2026-W05"
        assert result[-1]["week"] == "2026-W30"

    def test_chronological_order(self):
        week_data = {
            "2026-W06": {"training_days": 3, "total_sets": 15, "total_volume_kg": 5000.0, "exercises": {"squat"}},
            "2026-W04": {"training_days": 2, "total_sets": 10, "total_volume_kg": 3000.0, "exercises": {"bench"}},
            "2026-W05": {"training_days": 1, "total_sets": 5, "total_volume_kg": 1500.0, "exercises": {"deadlift"}},
        }
        result = _compute_weekly_summary(week_data)
        weeks = [r["week"] for r in result]
        assert weeks == ["2026-W04", "2026-W05", "2026-W06"]

    def test_empty(self):
        assert _compute_weekly_summary({}) == []


class TestComputeFrequency:
    def test_regular_training(self):
        # 3 days per week for 4 weeks
        ref = date(2026, 2, 8)
        training_dates = set()
        for week_offset in range(4):
            base = ref - __import__("datetime").timedelta(weeks=week_offset)
            for day_offset in [0, 2, 4]:
                training_dates.add(base - __import__("datetime").timedelta(days=day_offset))

        result = _compute_frequency(training_dates, ref)
        assert result["last_4_weeks"] == 3.0

    def test_no_training(self):
        result = _compute_frequency(set(), date(2026, 2, 8))
        assert result["last_4_weeks"] == 0.0
        assert result["last_12_weeks"] == 0.0

    def test_sparse_training(self):
        ref = date(2026, 2, 8)
        # Only 2 days in last 4 weeks
        training_dates = {
            date(2026, 2, 1),
            date(2026, 2, 5),
        }
        result = _compute_frequency(training_dates, ref)
        assert result["last_4_weeks"] == 0.5


class TestComputeStreak:
    def test_consecutive_weeks(self):
        # Training in W04, W05, W06 → current streak = 3
        training_dates = {
            date(2026, 1, 19),  # W04 Monday
            date(2026, 1, 26),  # W05 Monday
            date(2026, 2, 2),   # W06 Monday
        }
        ref = date(2026, 2, 2)
        result = _compute_streak(training_dates, ref)
        assert result["current_weeks"] == 3
        assert result["longest_weeks"] == 3

    def test_broken_streak(self):
        # W03, W04, gap W05, W06 → current = 1
        training_dates = {
            date(2026, 1, 12),  # W03 Monday
            date(2026, 1, 19),  # W04 Monday
            # gap in W05
            date(2026, 2, 2),   # W06 Monday
        }
        ref = date(2026, 2, 2)
        result = _compute_streak(training_dates, ref)
        assert result["current_weeks"] == 1
        assert result["longest_weeks"] == 2

    def test_no_training(self):
        result = _compute_streak(set(), date(2026, 2, 8))
        assert result["current_weeks"] == 0
        assert result["longest_weeks"] == 0

    def test_single_day(self):
        training_dates = {date(2026, 2, 8)}
        result = _compute_streak(training_dates, date(2026, 2, 8))
        assert result["current_weeks"] == 1
        assert result["longest_weeks"] == 1

    def test_multiple_days_same_week(self):
        # Multiple days in W06, one in W05
        training_dates = {
            date(2026, 1, 26),  # W05 Monday
            date(2026, 2, 2),   # W06 Monday
            date(2026, 2, 4),   # W06 Wednesday
            date(2026, 2, 6),   # W06 Friday
        }
        ref = date(2026, 2, 6)
        result = _compute_streak(training_dates, ref)
        assert result["current_weeks"] == 2
        assert result["longest_weeks"] == 2

    def test_reference_date_not_in_training(self):
        # Reference date's week has no training → current streak = 0
        training_dates = {
            date(2026, 1, 19),  # W04
            date(2026, 1, 26),  # W05
        }
        ref = date(2026, 2, 8)  # W06, no training
        result = _compute_streak(training_dates, ref)
        assert result["current_weeks"] == 0
        assert result["longest_weeks"] == 2

    def test_long_historical_streak(self):
        # Current streak short but longest is longer
        training_dates = set()
        # 10-week streak starting W40 of 2025
        for i in range(10):
            d = date(2025, 10, 6) + __import__("datetime").timedelta(weeks=i)
            training_dates.add(d)
        # Gap
        # Then 2-week streak
        training_dates.add(date(2026, 2, 2))  # W06
        training_dates.add(date(2026, 2, 8))  # W06 (same week, still 1)
        training_dates.add(date(2026, 1, 26))  # W05

        ref = date(2026, 2, 8)
        result = _compute_streak(training_dates, ref)
        assert result["current_weeks"] == 2
        assert result["longest_weeks"] == 10
