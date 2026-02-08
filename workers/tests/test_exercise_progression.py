"""Tests for exercise_progression handler pure functions and data handling."""

import pytest

from kura_workers.handlers.exercise_progression import _epley_1rm, _resolve_exercise_key


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
        assert _resolve_exercise_key(data) == "barbell_back_squat"

    def test_exercise_fallback(self):
        data = {"exercise": "Kniebeuge"}
        assert _resolve_exercise_key(data) == "kniebeuge"

    def test_exercise_id_normalized(self):
        data = {"exercise_id": "  Barbell_Back_Squat  "}
        assert _resolve_exercise_key(data) == "barbell_back_squat"

    def test_exercise_normalized(self):
        data = {"exercise": "  SQUAT  "}
        assert _resolve_exercise_key(data) == "squat"

    def test_empty_exercise_id_falls_back(self):
        data = {"exercise_id": "", "exercise": "squat"}
        assert _resolve_exercise_key(data) == "squat"

    def test_no_fields(self):
        assert _resolve_exercise_key({}) is None

    def test_both_empty(self):
        data = {"exercise_id": "", "exercise": ""}
        assert _resolve_exercise_key(data) is None

    def test_whitespace_only(self):
        data = {"exercise_id": "   ", "exercise": "   "}
        assert _resolve_exercise_key(data) is None
