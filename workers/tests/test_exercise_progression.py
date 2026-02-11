"""Tests for exercise_progression handler pure functions and data handling."""

from kura_workers.handlers.exercise_progression import (
    _infer_rir_from_rpe,
    _iso_week,
    _manifest_contribution,
    _normalize_rir,
    _resolve_set_rir,
)
from kura_workers.utils import (
    epley_1rm,
    find_all_keys_for_canonical,
    resolve_exercise_key,
    resolve_through_aliases,
)
from datetime import date


class TestEpley1RM:
    def test_single_rep(self):
        assert epley_1rm(100.0, 1) == 100.0

    def test_five_reps(self):
        # 100 * (1 + 5/30) = 100 * 1.1667 = 116.67
        assert round(epley_1rm(100.0, 5), 2) == 116.67

    def test_ten_reps(self):
        # 80 * (1 + 10/30) = 80 * 1.3333 = 106.67
        assert round(epley_1rm(80.0, 10), 2) == 106.67

    def test_zero_reps(self):
        assert epley_1rm(100.0, 0) == 0.0

    def test_negative_reps(self):
        assert epley_1rm(100.0, -1) == 0.0

    def test_zero_weight(self):
        assert epley_1rm(0.0, 5) == 0.0

    def test_negative_weight(self):
        assert epley_1rm(-10.0, 5) == 0.0


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


class TestManifestContribution:
    def test_extracts_exercise_keys(self):
        rows = [
            {"key": "barbell_back_squat", "data": {}},
            {"key": "barbell_bench_press", "data": {}},
        ]
        result = _manifest_contribution(rows)
        assert result == {"exercises": ["barbell_back_squat", "barbell_bench_press"]}

    def test_empty_rows(self):
        assert _manifest_contribution([]) == {"exercises": []}

    def test_single_exercise(self):
        rows = [{"key": "deadlift", "data": {}}]
        result = _manifest_contribution(rows)
        assert result == {"exercises": ["deadlift"]}


# --- Alias resolution (pure functions) ---


class TestResolveThroughAliases:
    def test_known_alias(self):
        alias_map = {"kniebeuge": "barbell_back_squat"}
        assert resolve_through_aliases("kniebeuge", alias_map) == "barbell_back_squat"

    def test_unknown_key_unchanged(self):
        alias_map = {"kniebeuge": "barbell_back_squat"}
        assert resolve_through_aliases("bench_press", alias_map) == "bench_press"

    def test_canonical_unchanged(self):
        alias_map = {"kniebeuge": "barbell_back_squat"}
        assert resolve_through_aliases("barbell_back_squat", alias_map) == "barbell_back_squat"

    def test_empty_map(self):
        assert resolve_through_aliases("anything", {}) == "anything"

    def test_multiple_aliases_same_target(self):
        alias_map = {
            "kniebeuge": "barbell_back_squat",
            "sq": "barbell_back_squat",
            "squats": "barbell_back_squat",
        }
        assert resolve_through_aliases("sq", alias_map) == "barbell_back_squat"
        assert resolve_through_aliases("kniebeuge", alias_map) == "barbell_back_squat"


class TestFindAllKeysForCanonical:
    def test_with_aliases(self):
        alias_map = {
            "kniebeuge": "barbell_back_squat",
            "sq": "barbell_back_squat",
            "bp": "barbell_bench_press",
        }
        result = find_all_keys_for_canonical("barbell_back_squat", alias_map)
        assert result == {"barbell_back_squat", "kniebeuge", "sq"}

    def test_no_aliases(self):
        alias_map = {"bp": "barbell_bench_press"}
        result = find_all_keys_for_canonical("barbell_back_squat", alias_map)
        assert result == {"barbell_back_squat"}

    def test_empty_map(self):
        result = find_all_keys_for_canonical("squat", {})
        assert result == {"squat"}

    def test_canonical_always_included(self):
        alias_map = {"kniebeuge": "barbell_back_squat"}
        result = find_all_keys_for_canonical("barbell_back_squat", alias_map)
        assert "barbell_back_squat" in result
        assert "kniebeuge" in result


class TestRirHelpers:
    def test_normalize_rir_bounds_and_rounding(self):
        assert _normalize_rir(-2) == 0.0
        assert _normalize_rir(3.1415) == 3.14
        assert _normalize_rir(12) == 10.0

    def test_infer_rir_from_rpe(self):
        assert _infer_rir_from_rpe(8.0) == 2.0
        assert _infer_rir_from_rpe(None) is None

    def test_resolve_set_rir_prefers_explicit(self):
        rir, source = _resolve_set_rir({"rir": 1, "rpe": 8}, 8.0)
        assert rir == 1.0
        assert source == "explicit"

    def test_resolve_set_rir_falls_back_to_rpe(self):
        rir, source = _resolve_set_rir({"rpe": 8}, 8.0)
        assert rir == 2.0
        assert source == "inferred_from_rpe"
