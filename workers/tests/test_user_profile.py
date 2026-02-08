"""Tests for user_profile three-layer structure (Decision 7)."""

from datetime import datetime, timezone

from kura_workers.handlers.user_profile import (
    _build_agenda,
    _build_data_quality,
    _build_system_layer,
    _build_user_dimensions,
    _find_unconfirmed_aliases,
    _resolve_exercises,
)


# --- TestBuildSystemLayer ---


class TestBuildSystemLayer:
    def test_builds_from_metadata(self):
        meta = {
            "exercise_progression": {
                "name": "exercise_progression",
                "description": "Strength progression",
                "key_structure": "one per exercise",
                "granularity": ["set", "week"],
                "event_types": ["set.logged"],
                "relates_to": {"training_timeline": {"join": "week", "why": "test"}},
                "manifest_contribution": lambda rows: {},
            },
        }
        result = _build_system_layer(meta)
        dim = result["dimensions"]["exercise_progression"]
        assert dim["description"] == "Strength progression"
        assert dim["key_structure"] == "one per exercise"
        assert dim["granularity"] == ["set", "week"]
        assert dim["event_types"] == ["set.logged"]
        assert dim["relates_to"] == {"training_timeline": {"join": "week", "why": "test"}}

    def test_strips_non_serializable_fields(self):
        meta = {
            "test_dim": {
                "name": "test_dim",
                "description": "Test",
                "manifest_contribution": lambda rows: {},
                "event_types": ["a.b"],
            },
        }
        result = _build_system_layer(meta)
        dim = result["dimensions"]["test_dim"]
        assert "manifest_contribution" not in dim
        assert "name" not in dim

    def test_includes_time_conventions(self):
        result = _build_system_layer({})
        assert "time_conventions" in result
        assert result["time_conventions"]["week"] == "ISO 8601 (2026-W06)"

    def test_empty_metadata(self):
        result = _build_system_layer({})
        assert result["dimensions"] == {}

    def test_multiple_dimensions(self):
        meta = {
            "dim_a": {"name": "dim_a", "description": "A", "event_types": ["x"]},
            "dim_b": {"name": "dim_b", "description": "B", "event_types": ["y"]},
        }
        result = _build_system_layer(meta)
        assert len(result["dimensions"]) == 2
        assert "dim_a" in result["dimensions"]
        assert "dim_b" in result["dimensions"]


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


class TestThreeLayerOutput:
    """Test that the assembled structure has the correct shape."""

    def test_structure_has_all_layers(self):
        system = _build_system_layer({})
        data_quality = _build_data_quality(0, 0, [], {}, [])
        dimensions = _build_user_dimensions({}, [], None)
        agenda = _build_agenda([], [])

        projection_data = {
            "system": system,
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

        assert "system" in projection_data
        assert "user" in projection_data
        assert "agenda" in projection_data
        assert "dimensions" in projection_data["system"]
        assert "time_conventions" in projection_data["system"]
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
        system = _build_system_layer(meta)
        assert system["dimensions"]["exercise_progression"]["description"] == "Strength progression"

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
