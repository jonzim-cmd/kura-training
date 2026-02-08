"""Tests for user_profile alias resolution and manifest logic."""


def _resolve_exercises(exercises_logged: set[str], aliases: dict[str, str]) -> set[str]:
    """Extract the alias resolution logic for testing."""
    alias_lookup = {a.strip().lower(): target for a, target in aliases.items()}
    return {alias_lookup.get(ex, ex) for ex in exercises_logged}


def _compute_data_quality(
    events: list[dict],
    aliases: dict[str, str],
) -> dict:
    """Extract the data_quality computation logic for testing.

    Mirrors the logic in user_profile handler:
    - events_without_exercise_id: set.logged events missing exercise_id
    - unresolved_exercises: exercises with no exercise_id AND not in alias map
    """
    alias_lookup = {a.strip().lower(): target for a, target in aliases.items()}
    events_without_exercise_id = 0
    raw_exercises_without_id: set[str] = set()

    for event in events:
        exercise_id = event.get("exercise_id", "").strip()
        exercise = event.get("exercise", "").strip().lower()
        if not exercise_id:
            events_without_exercise_id += 1
            if exercise:
                raw_exercises_without_id.add(exercise)

    unresolved = sorted(
        ex for ex in raw_exercises_without_id
        if ex not in alias_lookup
    )

    return {
        "events_without_exercise_id": events_without_exercise_id,
        "unresolved_exercises": unresolved,
    }


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


class TestDataQuality:
    def test_all_events_have_exercise_id(self):
        events = [
            {"exercise_id": "squat", "exercise": "Kniebeuge", "weight_kg": 100, "reps": 5},
            {"exercise_id": "bench", "weight_kg": 80, "reps": 8},
        ]
        result = _compute_data_quality(events, {})
        assert result["events_without_exercise_id"] == 0
        assert result["unresolved_exercises"] == []

    def test_events_without_exercise_id(self):
        events = [
            {"exercise": "squat", "weight_kg": 100, "reps": 5},
            {"exercise_id": "bench", "weight_kg": 80, "reps": 8},
            {"exercise": "deadlift", "weight_kg": 120, "reps": 3},
        ]
        result = _compute_data_quality(events, {})
        assert result["events_without_exercise_id"] == 2
        assert result["unresolved_exercises"] == ["deadlift", "squat"]

    def test_unresolved_filtered_by_aliases(self):
        events = [
            {"exercise": "kniebeuge", "weight_kg": 100, "reps": 5},
            {"exercise": "that weird cable thing", "weight_kg": 20, "reps": 12},
        ]
        aliases = {"Kniebeuge": "barbell_back_squat"}
        result = _compute_data_quality(events, aliases)
        assert result["events_without_exercise_id"] == 2
        # kniebeuge is resolved via alias, only cable thing remains
        assert result["unresolved_exercises"] == ["that weird cable thing"]

    def test_empty_events(self):
        result = _compute_data_quality([], {})
        assert result["events_without_exercise_id"] == 0
        assert result["unresolved_exercises"] == []

    def test_deduplication(self):
        # Same exercise appears multiple times without exercise_id
        events = [
            {"exercise": "squat", "weight_kg": 100, "reps": 5},
            {"exercise": "squat", "weight_kg": 105, "reps": 5},
            {"exercise": "squat", "weight_kg": 110, "reps": 3},
        ]
        result = _compute_data_quality(events, {})
        assert result["events_without_exercise_id"] == 3
        # Only one unique unresolved exercise
        assert result["unresolved_exercises"] == ["squat"]
