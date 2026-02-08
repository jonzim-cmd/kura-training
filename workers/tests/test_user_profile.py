"""Tests for user_profile alias resolution logic."""


def _resolve_exercises(exercises_logged: set[str], aliases: dict[str, str]) -> set[str]:
    """Extract the alias resolution logic for testing."""
    alias_lookup = {a.strip().lower(): target for a, target in aliases.items()}
    return {alias_lookup.get(ex, ex) for ex in exercises_logged}


class TestAliasResolution:
    def test_alias_resolves(self):
        exercises = {"kniebeuge", "barbell_bench_press"}
        aliases = {"Kniebeuge": "barbell_back_squat"}
        result = _resolve_exercises(exercises, aliases)
        assert result == {"barbell_back_squat", "barbell_bench_press"}

    def test_canonical_id_untouched(self):
        exercises = {"barbell_back_squat"}
        aliases = {"Kniebeuge": "barbell_back_squat"}
        result = _resolve_exercises(exercises, aliases)
        assert result == {"barbell_back_squat"}

    def test_multiple_aliases_same_target(self):
        exercises = {"kniebeuge", "sq", "squats"}
        aliases = {
            "Kniebeuge": "barbell_back_squat",
            "SQ": "barbell_back_squat",
            "Squats": "barbell_back_squat",
        }
        result = _resolve_exercises(exercises, aliases)
        assert result == {"barbell_back_squat"}

    def test_no_aliases(self):
        exercises = {"squat", "bench"}
        result = _resolve_exercises(exercises, {})
        assert result == {"squat", "bench"}

    def test_empty_exercises(self):
        result = _resolve_exercises(set(), {"Kniebeuge": "barbell_back_squat"})
        assert result == set()

    def test_mixed_resolved_and_unresolved(self):
        exercises = {"kniebeuge", "some_unknown_exercise", "barbell_back_squat"}
        aliases = {"Kniebeuge": "barbell_back_squat"}
        result = _resolve_exercises(exercises, aliases)
        # kniebeuge resolved to barbell_back_squat (deduped with existing), unknown stays
        assert result == {"barbell_back_squat", "some_unknown_exercise"}
