"""Tests for system_config — deployment-static configuration.

Tests build_system_config(), build_dimensions(), and _get_conventions().
These were previously in test_user_profile.py (TestBuildSystemLayer, TestConventions).
"""

from kura_workers.system_config import (
    _get_conventions,
    build_dimensions,
    build_system_config,
)


# --- TestBuildDimensions ---


class TestBuildDimensions:
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
        result = build_dimensions(meta)
        dim = result["exercise_progression"]
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
        result = build_dimensions(meta)
        dim = result["test_dim"]
        assert "manifest_contribution" not in dim
        assert "name" not in dim

    def test_includes_context_seeds(self):
        meta = {
            "dim_a": {
                "name": "dim_a",
                "description": "A",
                "event_types": ["x"],
                "context_seeds": ["experience_level", "training_modality"],
            },
        }
        result = build_dimensions(meta)
        assert result["dim_a"]["context_seeds"] == ["experience_level", "training_modality"]

    def test_omits_context_seeds_when_not_declared(self):
        meta = {
            "dim_a": {"name": "dim_a", "description": "A", "event_types": ["x"]},
        }
        result = build_dimensions(meta)
        assert "context_seeds" not in result["dim_a"]

    def test_default_projection_key(self):
        meta = {
            "dim_a": {"name": "dim_a", "description": "A", "event_types": ["x"]},
        }
        result = build_dimensions(meta)
        assert result["dim_a"]["projection_key"] == "overview"

    def test_custom_projection_key(self):
        meta = {
            "dim_a": {
                "name": "dim_a",
                "description": "A",
                "event_types": ["x"],
                "projection_key": "<exercise_id>",
            },
        }
        result = build_dimensions(meta)
        assert result["dim_a"]["projection_key"] == "<exercise_id>"

    def test_empty_metadata(self):
        result = build_dimensions({})
        assert result == {}

    def test_multiple_dimensions(self):
        meta = {
            "dim_a": {"name": "dim_a", "description": "A", "event_types": ["x"]},
            "dim_b": {"name": "dim_b", "description": "B", "event_types": ["y"]},
        }
        result = build_dimensions(meta)
        assert len(result) == 2
        assert "dim_a" in result
        assert "dim_b" in result


# --- TestBuildSystemConfig ---


class TestBuildSystemConfig:
    def test_has_all_sections(self):
        result = build_system_config()
        assert "dimensions" in result
        assert "event_conventions" in result
        assert "conventions" in result
        assert "time_conventions" in result
        assert "interview_guide" in result

    def test_time_conventions(self):
        result = build_system_config()
        assert result["time_conventions"]["week"] == "ISO 8601 (2026-W06)"
        assert result["time_conventions"]["date"] == "ISO 8601 (2026-02-08)"

    def test_event_conventions_count(self):
        result = build_system_config()
        assert len(result["event_conventions"]) == 19

    def test_interview_guide_structure(self):
        result = build_system_config()
        guide = result["interview_guide"]
        assert "philosophy" in guide
        assert "phases" in guide
        assert "coverage_areas" in guide

    def test_conventions_present(self):
        result = build_system_config()
        assert "exercise_normalization" in result["conventions"]


# --- TestConventions ---


class TestConventions:
    def test_has_exercise_normalization(self):
        result = _get_conventions()
        assert "exercise_normalization" in result

    def test_exercise_normalization_has_rules(self):
        result = _get_conventions()
        rules = result["exercise_normalization"]["rules"]
        assert isinstance(rules, list)
        assert len(rules) >= 3

    def test_exercise_normalization_has_example_batch(self):
        result = _get_conventions()
        batch = result["exercise_normalization"]["example_batch"]
        assert isinstance(batch, list)
        assert len(batch) == 2
        event_types = [e["event_type"] for e in batch]
        assert "set.logged" in event_types
        assert "exercise.alias_created" in event_types

    def test_example_batch_set_logged_has_exercise_id(self):
        result = _get_conventions()
        set_event = next(
            e for e in result["exercise_normalization"]["example_batch"]
            if e["event_type"] == "set.logged"
        )
        assert "exercise_id" in set_event["data"]
        assert "exercise" in set_event["data"]

    def test_rules_mention_exercise_id(self):
        result = _get_conventions()
        rules_text = " ".join(result["exercise_normalization"]["rules"]).lower()
        assert "exercise_id" in rules_text

    def test_rules_mention_aliases(self):
        result = _get_conventions()
        rules_text = " ".join(result["exercise_normalization"]["rules"]).lower()
        assert "alias" in rules_text

    def test_has_data_correction(self):
        result = _get_conventions()
        assert "data_correction" in result

    def test_data_correction_has_rules(self):
        result = _get_conventions()
        rules = result["data_correction"]["rules"]
        assert isinstance(rules, list)
        assert len(rules) >= 2

    def test_data_correction_has_example_batch(self):
        result = _get_conventions()
        batch = result["data_correction"]["example_batch"]
        assert isinstance(batch, list)
        assert len(batch) == 2
        event_types = [e["event_type"] for e in batch]
        assert "event.retracted" in event_types

    def test_data_correction_example_has_retracted_fields(self):
        result = _get_conventions()
        retracted_event = next(
            e for e in result["data_correction"]["example_batch"]
            if e["event_type"] == "event.retracted"
        )
        assert "retracted_event_id" in retracted_event["data"]
        assert "retracted_event_type" in retracted_event["data"]
