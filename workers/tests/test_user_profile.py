"""Tests for user_profile dynamic context (Decision 7 + Decision 8).

System layer tests are in test_system_config.py — user_profile only produces
user + agenda (dynamic per-user data).
"""

from datetime import datetime, timezone

from kura_workers.handlers.user_profile import (
    _build_agenda,
    _build_data_quality,
    _build_user_dimensions,
    _compute_interview_coverage,
    _find_orphaned_event_types,
    _find_unconfirmed_aliases,
    _resolve_exercises,
    _should_suggest_onboarding,
    _should_suggest_refresh,
)


# --- TestAliasWithConfidence ---


class TestAliasWithConfidence:
    def test_resolve_with_confidence_format(self):
        exercises = {"kniebeuge", "barbell_bench_press"}
        aliases = {"Kniebeuge": {"target": "barbell_back_squat", "confidence": "confirmed"}}
        result = _resolve_exercises(exercises, aliases)
        assert result == {"barbell_back_squat", "barbell_bench_press"}

    def test_multiple_aliases_same_target(self):
        exercises = {"kniebeuge", "sq", "squats"}
        aliases = {
            "Kniebeuge": {"target": "barbell_back_squat", "confidence": "confirmed"},
            "SQ": {"target": "barbell_back_squat", "confidence": "inferred"},
            "Squats": {"target": "barbell_back_squat", "confidence": "confirmed"},
        }
        result = _resolve_exercises(exercises, aliases)
        assert result == {"barbell_back_squat"}

    def test_no_aliases(self):
        result = _resolve_exercises({"squat", "bench"}, {})
        assert result == {"squat", "bench"}

    def test_empty_exercises(self):
        aliases = {"Kniebeuge": {"target": "barbell_back_squat", "confidence": "confirmed"}}
        result = _resolve_exercises(set(), aliases)
        assert result == set()

    def test_find_unconfirmed_aliases(self):
        aliases = {
            "Kniebeuge": {"target": "barbell_back_squat", "confidence": "confirmed"},
            "SQ": {"target": "barbell_back_squat", "confidence": "inferred"},
            "BP": {"target": "barbell_bench_press", "confidence": "inferred"},
        }
        result = _find_unconfirmed_aliases(aliases)
        assert len(result) == 2
        aliases_found = {r["alias"] for r in result}
        assert aliases_found == {"SQ", "BP"}
        for r in result:
            assert r["confidence"] == "inferred"

    def test_all_confirmed(self):
        aliases = {
            "Kniebeuge": {"target": "barbell_back_squat", "confidence": "confirmed"},
        }
        assert _find_unconfirmed_aliases(aliases) == []

    def test_empty_aliases(self):
        assert _find_unconfirmed_aliases({}) == []


# --- TestBuildUserDimensions ---


class TestBuildUserDimensions:
    def _make_meta(self, contrib_fn=None):
        return {
            "exercise_progression": {
                "name": "exercise_progression",
                "manifest_contribution": contrib_fn or (lambda rows: {"exercises": [r["key"] for r in rows]}),
                "event_types": ["set.logged"],
            },
            "training_timeline": {
                "name": "training_timeline",
                "manifest_contribution": contrib_fn or (lambda rows: {"last_training": "2026-02-08"} if rows else {}),
                "event_types": ["set.logged"],
            },
        }

    def test_active_dimensions(self):
        meta = self._make_meta()
        projection_rows = [
            {"projection_type": "exercise_progression", "key": "squat", "data": {}, "updated_at": datetime(2026, 2, 8, 14, 0, tzinfo=timezone.utc)},
            {"projection_type": "exercise_progression", "key": "bench", "data": {}, "updated_at": datetime(2026, 2, 7, 10, 0, tzinfo=timezone.utc)},
            {"projection_type": "training_timeline", "key": "overview", "data": {}, "updated_at": datetime(2026, 2, 8, 14, 0, tzinfo=timezone.utc)},
        ]
        result = _build_user_dimensions(meta, projection_rows, ("2025-06-15", "2026-02-08"))

        assert result["exercise_progression"]["status"] == "active"
        assert result["exercise_progression"]["exercises"] == ["squat", "bench"]
        assert result["exercise_progression"]["coverage"] == {"from": "2025-06-15", "to": "2026-02-08"}

        assert result["training_timeline"]["status"] == "active"
        assert result["training_timeline"]["last_training"] == "2026-02-08"

    def test_no_data_dimension(self):
        meta = self._make_meta()
        # Only exercise_progression has data, training_timeline does not
        projection_rows = [
            {"projection_type": "exercise_progression", "key": "squat", "data": {}, "updated_at": datetime(2026, 2, 8, tzinfo=timezone.utc)},
        ]
        result = _build_user_dimensions(meta, projection_rows, ("2025-06-15", "2026-02-08"))
        assert result["training_timeline"] == {"status": "no_data"}

    def test_freshness_uses_max(self):
        meta = self._make_meta()
        projection_rows = [
            {"projection_type": "exercise_progression", "key": "squat", "data": {}, "updated_at": datetime(2026, 2, 7, 10, 0, tzinfo=timezone.utc)},
            {"projection_type": "exercise_progression", "key": "bench", "data": {}, "updated_at": datetime(2026, 2, 8, 14, 0, tzinfo=timezone.utc)},
        ]
        result = _build_user_dimensions(meta, projection_rows, None)
        assert "2026-02-08T14:00:00" in result["exercise_progression"]["freshness"]

    def test_no_coverage_when_no_range(self):
        meta = self._make_meta()
        projection_rows = [
            {"projection_type": "exercise_progression", "key": "squat", "data": {}, "updated_at": datetime(2026, 2, 8, tzinfo=timezone.utc)},
        ]
        result = _build_user_dimensions(meta, projection_rows, None)
        assert "coverage" not in result["exercise_progression"]

    def test_empty_everything(self):
        result = _build_user_dimensions({}, [], None)
        assert result == {}


# --- TestDataQualityActionable ---


class TestDataQualityActionable:
    def test_unresolved_exercises(self):
        result = _build_data_quality(
            total_set_logged=20,
            events_without_exercise_id=7,
            unresolved_exercises=["that weird cable thing"],
            exercise_occurrences={"that weird cable thing": 7},
            unconfirmed_aliases=[],
        )
        assert result["total_set_logged_events"] == 20
        assert result["events_without_exercise_id"] == 7
        assert len(result["actionable"]) == 1
        item = result["actionable"][0]
        assert item["type"] == "unresolved_exercise"
        assert item["exercise"] == "that weird cable thing"
        assert item["occurrences"] == 7

    def test_unconfirmed_aliases(self):
        result = _build_data_quality(
            total_set_logged=10,
            events_without_exercise_id=0,
            unresolved_exercises=[],
            exercise_occurrences={},
            unconfirmed_aliases=[
                {"alias": "SQ", "target": "barbell_back_squat", "confidence": "inferred"},
            ],
        )
        assert len(result["actionable"]) == 1
        item = result["actionable"][0]
        assert item["type"] == "unconfirmed_alias"
        assert item["alias"] == "SQ"
        assert item["confidence"] == "inferred"

    def test_both_types(self):
        result = _build_data_quality(
            total_set_logged=30,
            events_without_exercise_id=5,
            unresolved_exercises=["cable_thing", "mystery_move"],
            exercise_occurrences={"cable_thing": 3, "mystery_move": 2},
            unconfirmed_aliases=[
                {"alias": "SQ", "target": "barbell_back_squat", "confidence": "inferred"},
            ],
        )
        assert len(result["actionable"]) == 3
        types = [a["type"] for a in result["actionable"]]
        assert types.count("unresolved_exercise") == 2
        assert types.count("unconfirmed_alias") == 1

    def test_clean_data(self):
        result = _build_data_quality(
            total_set_logged=50,
            events_without_exercise_id=0,
            unresolved_exercises=[],
            exercise_occurrences={},
            unconfirmed_aliases=[],
        )
        assert result["actionable"] == []
        assert result["total_set_logged_events"] == 50


# --- TestBuildAgenda ---


class TestBuildAgenda:
    def test_unresolved_exercises_single(self):
        result = _build_agenda(
            unresolved_exercises=[{"exercise": "cable thing", "occurrences": 7}],
            unconfirmed_aliases=[],
        )
        assert len(result) == 1
        item = result[0]
        assert item["priority"] == "medium"
        assert item["type"] == "resolve_exercises"
        assert "7 sets" in item["detail"]
        assert "cable thing" in item["detail"]
        assert item["dimensions"] == ["user_profile"]

    def test_unresolved_exercises_multiple(self):
        result = _build_agenda(
            unresolved_exercises=[
                {"exercise": "cable thing", "occurrences": 7},
                {"exercise": "mystery move", "occurrences": 3},
            ],
            unconfirmed_aliases=[],
        )
        assert len(result) == 1
        assert "10 sets" in result[0]["detail"]
        assert "2 unresolved exercises" in result[0]["detail"]

    def test_unconfirmed_alias(self):
        result = _build_agenda(
            unresolved_exercises=[],
            unconfirmed_aliases=[
                {"alias": "SQ", "target": "barbell_back_squat", "confidence": "inferred"},
            ],
        )
        assert len(result) == 1
        item = result[0]
        assert item["priority"] == "low"
        assert item["type"] == "confirm_alias"
        assert "SQ" in item["detail"]
        assert "inferred" in item["detail"]
        assert item["dimensions"] == ["user_profile"]

    def test_empty_agenda(self):
        result = _build_agenda([], [])
        assert result == []

    def test_combined(self):
        result = _build_agenda(
            unresolved_exercises=[{"exercise": "x", "occurrences": 1}],
            unconfirmed_aliases=[
                {"alias": "A", "target": "t", "confidence": "inferred"},
                {"alias": "B", "target": "u", "confidence": "inferred"},
            ],
        )
        assert len(result) == 3  # 1 resolve + 2 confirm
        types = [a["type"] for a in result]
        assert types[0] == "resolve_exercises"
        assert types[1] == "confirm_alias"
        assert types[2] == "confirm_alias"


# --- TestThreeLayerOutput ---


class TestProjectionOutput:
    """Test that the assembled projection has the correct shape (user + agenda)."""

    def test_structure_has_user_and_agenda(self):
        data_quality = _build_data_quality(0, 0, [], {}, [])
        dimensions = _build_user_dimensions({}, [], None)
        agenda = _build_agenda([], [])

        projection_data = {
            "user": {
                "aliases": {},
                "preferences": {},
                "goals": [],
                "exercises_logged": [],
                "total_events": 0,
                "first_event": "2026-02-08T00:00:00+00:00",
                "last_event": "2026-02-08T00:00:00+00:00",
                "dimensions": dimensions,
                "data_quality": data_quality,
            },
            "agenda": agenda,
        }

        assert "user" in projection_data
        assert "agenda" in projection_data
        assert "system" not in projection_data
        assert "aliases" in projection_data["user"]
        assert "dimensions" in projection_data["user"]
        assert "data_quality" in projection_data["user"]
        assert isinstance(projection_data["agenda"], list)

    def test_full_realistic_output(self):
        meta = {
            "exercise_progression": {
                "name": "exercise_progression",
                "description": "Strength progression",
                "key_structure": "one per exercise",
                "granularity": ["set", "week"],
                "event_types": ["set.logged"],
                "relates_to": {"training_timeline": {"join": "week", "why": "test"}},
                "manifest_contribution": lambda rows: {"exercises": [r["key"] for r in rows]},
            },
        }

        projection_rows = [
            {
                "projection_type": "exercise_progression",
                "key": "barbell_back_squat",
                "data": {"exercise": "barbell_back_squat", "estimated_1rm": 120.0},
                "updated_at": datetime(2026, 2, 8, 14, 0, tzinfo=timezone.utc),
            },
        ]
        dimensions = _build_user_dimensions(meta, projection_rows, ("2025-06-15", "2026-02-08"))
        assert dimensions["exercise_progression"]["status"] == "active"
        assert dimensions["exercise_progression"]["exercises"] == ["barbell_back_squat"]

        data_quality = _build_data_quality(
            total_set_logged=50,
            events_without_exercise_id=3,
            unresolved_exercises=["cable_thing"],
            exercise_occurrences={"cable_thing": 3},
            unconfirmed_aliases=[],
        )
        assert data_quality["actionable"][0]["occurrences"] == 3

        agenda = _build_agenda(
            [{"exercise": "cable_thing", "occurrences": 3}],
            [],
        )
        assert agenda[0]["type"] == "resolve_exercises"


# --- TestInterviewCoverage (Decision 8) ---


class TestInterviewCoverage:
    def test_all_uncovered_for_empty_user(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[], profile_data={}, injuries=[],
        )
        assert len(result) == 9  # All coverage areas
        for item in result:
            assert item["status"] == "uncovered"

    def test_training_background_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[],
            profile_data={"training_modality": "strength"}, injuries=[],
        )
        bg = next(c for c in result if c["area"] == "training_background")
        assert bg["status"] == "covered"

    def test_training_background_covered_by_experience(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[],
            profile_data={"experience_level": "intermediate"}, injuries=[],
        )
        bg = next(c for c in result if c["area"] == "training_background")
        assert bg["status"] == "covered"

    def test_goals_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={},
            goals=[{"goal_type": "strength"}],
            profile_data={}, injuries=[],
        )
        goals = next(c for c in result if c["area"] == "goals")
        assert goals["status"] == "covered"

    def test_exercise_vocabulary_needs_depth(self):
        aliases = {
            "SQ": {"target": "barbell_back_squat", "confidence": "confirmed"},
            "BP": {"target": "barbell_bench_press", "confidence": "confirmed"},
        }
        result = _compute_interview_coverage(
            aliases=aliases, preferences={}, goals=[], profile_data={}, injuries=[],
        )
        vocab = next(c for c in result if c["area"] == "exercise_vocabulary")
        assert vocab["status"] == "needs_depth"
        assert "2 aliases" in vocab["note"]

    def test_exercise_vocabulary_covered_with_three(self):
        aliases = {
            "SQ": {"target": "barbell_back_squat", "confidence": "confirmed"},
            "BP": {"target": "barbell_bench_press", "confidence": "confirmed"},
            "DL": {"target": "barbell_deadlift", "confidence": "confirmed"},
        }
        result = _compute_interview_coverage(
            aliases=aliases, preferences={}, goals=[], profile_data={}, injuries=[],
        )
        vocab = next(c for c in result if c["area"] == "exercise_vocabulary")
        assert vocab["status"] == "covered"

    def test_unit_preferences_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={"unit_system": "metric"}, goals=[],
            profile_data={}, injuries=[],
        )
        units = next(c for c in result if c["area"] == "unit_preferences")
        assert units["status"] == "covered"

    def test_injuries_covered_by_report(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[], profile_data={},
            injuries=[{"description": "knee pain", "severity": "mild"}],
        )
        inj = next(c for c in result if c["area"] == "injuries")
        assert inj["status"] == "covered"

    def test_injuries_covered_by_none_flag(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[],
            profile_data={"injuries_none": True}, injuries=[],
        )
        inj = next(c for c in result if c["area"] == "injuries")
        assert inj["status"] == "covered"

    def test_equipment_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[],
            profile_data={"available_equipment": ["barbell", "rack"]}, injuries=[],
        )
        eq = next(c for c in result if c["area"] == "equipment")
        assert eq["status"] == "covered"

    def test_schedule_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[],
            profile_data={"training_frequency_per_week": 4}, injuries=[],
        )
        sched = next(c for c in result if c["area"] == "schedule")
        assert sched["status"] == "covered"

    def test_nutrition_interest_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={"nutrition_tracking": "later"}, goals=[],
            profile_data={}, injuries=[],
        )
        nutr = next(c for c in result if c["area"] == "nutrition_interest")
        assert nutr["status"] == "covered"

    def test_current_program_covered(self):
        result = _compute_interview_coverage(
            aliases={}, preferences={}, goals=[],
            profile_data={"current_program": "5/3/1"}, injuries=[],
        )
        prog = next(c for c in result if c["area"] == "current_program")
        assert prog["status"] == "covered"

    def test_mixed_coverage(self):
        result = _compute_interview_coverage(
            aliases={"SQ": {"target": "squat", "confidence": "confirmed"}},
            preferences={"unit_system": "metric"},
            goals=[{"goal_type": "strength"}],
            profile_data={"training_modality": "strength"},
            injuries=[],
        )
        statuses = {c["area"]: c["status"] for c in result}
        assert statuses["training_background"] == "covered"
        assert statuses["goals"] == "covered"
        assert statuses["unit_preferences"] == "covered"
        assert statuses["exercise_vocabulary"] == "needs_depth"
        assert statuses["injuries"] == "uncovered"
        assert statuses["equipment"] == "uncovered"


# --- TestOnboardingTrigger ---


class TestOnboardingTrigger:
    def _all_uncovered(self):
        return [{"area": f"area_{i}", "status": "uncovered"} for i in range(9)]

    def _mostly_covered(self):
        return [
            {"area": "a", "status": "covered"},
            {"area": "b", "status": "covered"},
            {"area": "c", "status": "covered"},
            {"area": "d", "status": "covered"},
            {"area": "e", "status": "uncovered"},
        ]

    def test_new_user_triggers_onboarding(self):
        assert _should_suggest_onboarding(0, self._all_uncovered()) is True

    def test_few_events_triggers_onboarding(self):
        assert _should_suggest_onboarding(3, self._all_uncovered()) is True

    def test_enough_events_no_onboarding(self):
        assert _should_suggest_onboarding(5, self._all_uncovered()) is False

    def test_mostly_covered_no_onboarding(self):
        assert _should_suggest_onboarding(2, self._mostly_covered()) is False

    def test_refresh_with_many_events_and_gaps(self):
        coverage = [{"area": f"a{i}", "status": "uncovered"} for i in range(4)]
        assert _should_suggest_refresh(25, coverage, has_goals=False, has_preferences=True) is True

    def test_no_refresh_for_new_user(self):
        coverage = [{"area": f"a{i}", "status": "uncovered"} for i in range(4)]
        assert _should_suggest_refresh(10, coverage, has_goals=False, has_preferences=False) is False

    def test_no_refresh_when_covered(self):
        coverage = [{"area": "a", "status": "covered"}, {"area": "b", "status": "covered"}]
        assert _should_suggest_refresh(50, coverage, has_goals=True, has_preferences=True) is False

    def test_no_refresh_when_goals_and_prefs_present(self):
        coverage = [{"area": f"a{i}", "status": "uncovered"} for i in range(4)]
        assert _should_suggest_refresh(25, coverage, has_goals=True, has_preferences=True) is False


# --- TestBuildAgendaWithInterview ---


class TestBuildAgendaWithInterview:
    def test_onboarding_in_agenda(self):
        coverage = [{"area": f"a{i}", "status": "uncovered"} for i in range(9)]
        result = _build_agenda(
            [], [], interview_coverage=coverage, total_events=0,
            has_goals=False, has_preferences=False,
        )
        types = [a["type"] for a in result]
        assert "onboarding_needed" in types
        item = next(a for a in result if a["type"] == "onboarding_needed")
        assert item["priority"] == "high"

    def test_refresh_in_agenda(self):
        coverage = [{"area": f"a{i}", "status": "uncovered"} for i in range(4)]
        result = _build_agenda(
            [], [], interview_coverage=coverage, total_events=30,
            has_goals=False, has_preferences=True,
        )
        types = [a["type"] for a in result]
        assert "profile_refresh_suggested" in types

    def test_no_interview_items_when_covered(self):
        coverage = [{"area": "a", "status": "covered"}, {"area": "b", "status": "covered"}]
        result = _build_agenda(
            [], [], interview_coverage=coverage, total_events=50,
            has_goals=True, has_preferences=True,
        )
        types = [a["type"] for a in result]
        assert "onboarding_needed" not in types
        assert "profile_refresh_suggested" not in types

    def test_interview_items_come_before_data_quality(self):
        coverage = [{"area": f"a{i}", "status": "uncovered"} for i in range(9)]
        result = _build_agenda(
            [{"exercise": "x", "occurrences": 1}],
            [],
            interview_coverage=coverage,
            total_events=0,
            has_goals=False,
            has_preferences=False,
        )
        assert result[0]["type"] == "onboarding_needed"
        assert result[1]["type"] == "resolve_exercises"

    def test_backwards_compatible_without_coverage(self):
        """_build_agenda still works when called without interview_coverage."""
        result = _build_agenda(
            [{"exercise": "x", "occurrences": 1}],
            [],
        )
        assert len(result) == 1
        assert result[0]["type"] == "resolve_exercises"


# --- TestOrphanedEventTypes (Decision 9) ---


class TestOrphanedEventTypes:
    def test_detects_orphaned_types(self):
        # Simulate a user who has sent event types not handled by any handler
        all_event_types = {
            "set.logged": 50,
            "preference.set": 3,
            "mobility.logged": 23,
            "meditation.logged": 5,
        }
        result = _find_orphaned_event_types(all_event_types)
        orphaned_types = {o["event_type"] for o in result}
        assert "mobility.logged" in orphaned_types
        assert "meditation.logged" in orphaned_types
        assert "set.logged" not in orphaned_types
        assert "preference.set" not in orphaned_types

    def test_no_orphans_when_all_handled(self):
        all_event_types = {
            "set.logged": 50,
            "preference.set": 3,
        }
        result = _find_orphaned_event_types(all_event_types)
        assert result == []

    def test_empty_event_types(self):
        result = _find_orphaned_event_types({})
        assert result == []

    def test_preserves_counts(self):
        all_event_types = {"unknown.event": 42}
        result = _find_orphaned_event_types(all_event_types)
        assert len(result) == 1
        assert result[0]["event_type"] == "unknown.event"
        assert result[0]["count"] == 42


class TestDataQualityWithOrphans:
    def test_orphaned_event_types_in_data_quality(self):
        result = _build_data_quality(
            total_set_logged=10,
            events_without_exercise_id=0,
            unresolved_exercises=[],
            exercise_occurrences={},
            unconfirmed_aliases=[],
            orphaned_event_types=[{"event_type": "mobility.logged", "count": 23}],
        )
        assert "orphaned_event_types" in result
        assert result["orphaned_event_types"][0]["event_type"] == "mobility.logged"

    def test_no_orphaned_key_when_empty(self):
        result = _build_data_quality(
            total_set_logged=10,
            events_without_exercise_id=0,
            unresolved_exercises=[],
            exercise_occurrences={},
            unconfirmed_aliases=[],
            orphaned_event_types=[],
        )
        assert "orphaned_event_types" not in result

    def test_no_orphaned_key_when_none(self):
        result = _build_data_quality(
            total_set_logged=10,
            events_without_exercise_id=0,
            unresolved_exercises=[],
            exercise_occurrences={},
            unconfirmed_aliases=[],
        )
        assert "orphaned_event_types" not in result
