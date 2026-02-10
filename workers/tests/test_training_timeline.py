"""Tests for training_timeline handler pure functions."""

from datetime import date

from kura_workers.handlers.training_timeline import (
    _compute_frequency,
    _compute_recent_days,
    _compute_recent_sessions,
    _compute_streak,
    _compute_weekly_summary,
    _iso_week,
    _manifest_contribution,
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

    def test_top_sets_included(self):
        day_data = {
            date(2026, 2, 8): {
                "exercises": {"squat", "bench"},
                "total_sets": 6,
                "total_volume_kg": 2400.0,
                "total_reps": 30,
                "top_sets": {
                    "squat": {"weight_kg": 100, "reps": 5, "estimated_1rm": 116.7},
                    "bench": {"weight_kg": 80, "reps": 5, "estimated_1rm": 93.3},
                },
            }
        }
        result = _compute_recent_days(day_data)
        assert "top_sets" in result[0]
        assert result[0]["top_sets"]["squat"]["weight_kg"] == 100
        assert result[0]["top_sets"]["bench"]["estimated_1rm"] == 93.3

    def test_no_top_sets_when_empty(self):
        day_data = {
            date(2026, 2, 8): {
                "exercises": {"squat"},
                "total_sets": 1,
                "total_volume_kg": 0.0,
                "total_reps": 0,
                "top_sets": {},
            }
        }
        result = _compute_recent_days(day_data)
        assert "top_sets" not in result[0]

    def test_no_top_sets_when_missing(self):
        """Backward compat: old data without top_sets key."""
        day_data = {
            date(2026, 2, 8): {
                "exercises": {"squat"},
                "total_sets": 1,
                "total_volume_kg": 100.0,
                "total_reps": 5,
            }
        }
        result = _compute_recent_days(day_data)
        assert "top_sets" not in result[0]


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


class TestComputeRecentSessions:
    def test_single_session_with_id(self):
        session_data = {
            "morning-upper": {
                "date": "2026-02-08",
                "session_id": "morning-upper",
                "exercises": {"bench", "overhead_press"},
                "total_sets": 6,
                "total_volume_kg": 1800.0,
                "total_reps": 30,
            }
        }
        result = _compute_recent_sessions(session_data)
        assert len(result) == 1
        assert result[0]["session_id"] == "morning-upper"
        assert result[0]["date"] == "2026-02-08"
        assert result[0]["exercises"] == ["bench", "overhead_press"]

    def test_fallback_to_date_when_no_session_id(self):
        session_data = {
            "2026-02-08": {
                "date": "2026-02-08",
                "session_id": None,
                "exercises": {"squat"},
                "total_sets": 3,
                "total_volume_kg": 900.0,
                "total_reps": 15,
            }
        }
        result = _compute_recent_sessions(session_data)
        assert len(result) == 1
        assert "session_id" not in result[0]
        assert result[0]["date"] == "2026-02-08"

    def test_two_sessions_same_day(self):
        session_data = {
            "morning-upper": {
                "date": "2026-02-08",
                "session_id": "morning-upper",
                "exercises": {"bench"},
                "total_sets": 3,
                "total_volume_kg": 600.0,
                "total_reps": 15,
            },
            "evening-cardio": {
                "date": "2026-02-08",
                "session_id": "evening-cardio",
                "exercises": {"rowing"},
                "total_sets": 1,
                "total_volume_kg": 0.0,
                "total_reps": 0,
            },
        }
        result = _compute_recent_sessions(session_data)
        assert len(result) == 2
        assert result[0]["session_id"] == "evening-cardio"
        assert result[1]["session_id"] == "morning-upper"

    def test_chronological_order(self):
        session_data = {
            "s3": {"date": "2026-02-08", "session_id": "s3", "exercises": {"bench"}, "total_sets": 1, "total_volume_kg": 0.0, "total_reps": 5},
            "s1": {"date": "2026-02-06", "session_id": "s1", "exercises": {"squat"}, "total_sets": 1, "total_volume_kg": 0.0, "total_reps": 5},
            "s2": {"date": "2026-02-07", "session_id": "s2", "exercises": {"deadlift"}, "total_sets": 1, "total_volume_kg": 0.0, "total_reps": 5},
        }
        result = _compute_recent_sessions(session_data)
        dates = [r["date"] for r in result]
        assert dates == ["2026-02-06", "2026-02-07", "2026-02-08"]

    def test_max_30_sessions(self):
        session_data = {}
        for i in range(35):
            key = f"session-{i:03d}"
            d = f"2026-01-{(i % 28) + 1:02d}"
            session_data[key] = {
                "date": d,
                "session_id": key,
                "exercises": {"squat"},
                "total_sets": 1,
                "total_volume_kg": 100.0,
                "total_reps": 5,
            }
        result = _compute_recent_sessions(session_data)
        assert len(result) == 30

    def test_empty(self):
        assert _compute_recent_sessions({}) == []

    def test_top_sets_included(self):
        session_data = {
            "morning-upper": {
                "date": "2026-02-08",
                "session_id": "morning-upper",
                "exercises": {"bench", "overhead_press"},
                "total_sets": 6,
                "total_volume_kg": 1800.0,
                "total_reps": 30,
                "top_sets": {
                    "bench": {"weight_kg": 80, "reps": 5, "estimated_1rm": 93.3},
                    "overhead_press": {"weight_kg": 50, "reps": 8, "estimated_1rm": 63.3},
                },
            }
        }
        result = _compute_recent_sessions(session_data)
        assert "top_sets" in result[0]
        assert result[0]["top_sets"]["bench"]["weight_kg"] == 80

    def test_no_top_sets_when_empty(self):
        session_data = {
            "s1": {
                "date": "2026-02-08",
                "session_id": "s1",
                "exercises": {"stretching"},
                "total_sets": 1,
                "total_volume_kg": 0.0,
                "total_reps": 0,
                "top_sets": {},
            }
        }
        result = _compute_recent_sessions(session_data)
        assert "top_sets" not in result[0]


class TestManifestContribution:
    def test_extracts_summary(self):
        rows = [{"key": "overview", "data": {
            "last_training": "2026-02-08",
            "total_training_days": 127,
            "current_frequency": {"last_4_weeks": 3.25, "last_12_weeks": 2.8},
            "streak": {"current_weeks": 4, "longest_weeks": 12},
            "recent_days": [],
            "weekly_summary": [],
        }}]
        result = _manifest_contribution(rows)
        assert result["last_training"] == "2026-02-08"
        assert result["total_training_days"] == 127
        assert result["current_frequency"] == {"last_4_weeks": 3.25, "last_12_weeks": 2.8}
        assert result["streak"] == {"current_weeks": 4, "longest_weeks": 12}

    def test_empty_rows(self):
        assert _manifest_contribution([]) == {}

    def test_ignores_extra_fields(self):
        rows = [{"key": "overview", "data": {
            "last_training": "2026-02-08",
            "total_training_days": 5,
            "current_frequency": {"last_4_weeks": 1.0, "last_12_weeks": 0.5},
            "streak": {"current_weeks": 1, "longest_weeks": 1},
            "recent_days": [{"date": "2026-02-08", "exercises": ["squat"]}],
        }}]
        result = _manifest_contribution(rows)
        assert "recent_days" not in result
