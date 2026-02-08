"""Tests for exercise_progression handler pure functions and data handling."""

import pytest

from kura_workers.handlers.exercise_progression import _epley_1rm, _iso_week
from kura_workers.utils import resolve_exercise_key
from datetime import date


class TestEpley1RM:
    def test_single_rep(self):
        assert _epley_1rm(100.0, 1) == 100.0

    def test_five_reps(self):
        # 100 * (1 + 5/30) = 100 * 1.1667 = 116.67
        assert round(_epley_1rm(100.0, 5), 2) == 116.67

    def test_ten_reps(self):
        # 80 * (1 + 10/30) = 80 * 1.3333 = 106.67
        assert round(_epley_1rm(80.0, 10), 2) == 106.67

    def test_zero_reps(self):
        assert _epley_1rm(100.0, 0) == 0.0

    def test_negative_reps(self):
        assert _epley_1rm(100.0, -1) == 0.0

    def test_zero_weight(self):
        assert _epley_1rm(0.0, 5) == 0.0

    def test_negative_weight(self):
        assert _epley_1rm(-10.0, 5) == 0.0


class TestResolveExerciseKey:
    def test_exercise_id_preferred(self):
        data = {"exercise_id": "barbell_back_squat", "exercise": "Kniebeuge"}
        assert resolve_exercise_key(data) == "barbell_back_squat"

    def test_exercise_fallback(self):
        data = {"exercise": "Kniebeuge"}
        assert resolve_exercise_key(data) == "kniebeuge"

    def test_exercise_id_normalized(self):
        data = {"exercise_id": "  Barbell_Back_Squat  "}
        assert resolve_exercise_key(data) == "barbell_back_squat"

    def test_exercise_normalized(self):
        data = {"exercise": "  SQUAT  "}
        assert resolve_exercise_key(data) == "squat"

    def test_empty_exercise_id_falls_back(self):
        data = {"exercise_id": "", "exercise": "squat"}
        assert resolve_exercise_key(data) == "squat"

    def test_no_fields(self):
        assert resolve_exercise_key({}) is None

    def test_both_empty(self):
        data = {"exercise_id": "", "exercise": ""}
        assert resolve_exercise_key(data) is None

    def test_whitespace_only(self):
        data = {"exercise_id": "   ", "exercise": "   "}
        assert resolve_exercise_key(data) is None


class TestIsoWeek:
    def test_normal_date(self):
        assert _iso_week(date(2026, 2, 8)) == "2026-W06"

    def test_week_boundary(self):
        # Sunday and Monday of same ISO week
        assert _iso_week(date(2026, 2, 8)) == "2026-W06"  # Sunday
        assert _iso_week(date(2026, 2, 2)) == "2026-W06"  # Monday

    def test_year_boundary(self):
        # Dec 29, 2025 is Monday of ISO week 1 of 2026
        assert _iso_week(date(2025, 12, 29)) == "2026-W01"
