"""Tests for system_config — deployment-static configuration.

Tests build_system_config(), build_dimensions(), and _get_conventions().
These were previously in test_user_profile.py (TestBuildSystemLayer, TestConventions).
"""

from kura_workers.system_config import (
    _get_agent_behavior,
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

    def test_includes_output_schema(self):
        meta = {
            "dim_a": {
                "name": "dim_a",
                "description": "A",
                "event_types": ["x"],
                "output_schema": {"field_a": "number", "field_b": "string"},
            },
        }
        result = build_dimensions(meta)
        assert "output_schema" in result["dim_a"]
        assert result["dim_a"]["output_schema"]["field_a"] == "number"

    def test_omits_output_schema_when_not_declared(self):
        meta = {
            "dim_a": {"name": "dim_a", "description": "A", "event_types": ["x"]},
        }
        result = build_dimensions(meta)
        assert "output_schema" not in result["dim_a"]

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
        assert "agent_behavior" in result
        assert "projection_schemas" in result

    def test_time_conventions(self):
        result = build_system_config()
        assert result["time_conventions"]["week"] == "ISO 8601 (2026-W06)"
        assert result["time_conventions"]["date"] == "ISO 8601 (2026-02-08)"

    def test_event_conventions_count(self):
        result = build_system_config()
        assert len(result["event_conventions"]) == 22

    def test_interview_guide_structure(self):
        result = build_system_config()
        guide = result["interview_guide"]
        assert "philosophy" in guide
        assert "phases" in guide
        assert "coverage_areas" in guide

    def test_conventions_present(self):
        result = build_system_config()
        assert "exercise_normalization" in result["conventions"]

    def test_all_domain_dimensions_have_output_schema(self):
        result = build_system_config()
        dimensions = result["dimensions"]
        for name, dim in dimensions.items():
            assert "output_schema" in dim, f"Dimension '{name}' missing output_schema"
            assert isinstance(dim["output_schema"], dict), f"Dimension '{name}' output_schema not a dict"

    def test_projection_schemas_has_user_profile(self):
        result = build_system_config()
        schemas = result["projection_schemas"]
        assert "user_profile" in schemas
        assert schemas["user_profile"]["projection_key"] == "me"
        assert "output_schema" in schemas["user_profile"]

    def test_projection_schemas_has_custom_patterns(self):
        result = build_system_config()
        schemas = result["projection_schemas"]
        assert "custom" in schemas
        assert "patterns" in schemas["custom"]
        assert "field_tracking" in schemas["custom"]["patterns"]
        assert "categorized_tracking" in schemas["custom"]["patterns"]


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

    def test_has_semantic_resolution_conventions(self):
        result = _get_conventions()
        assert "semantic_resolution" in result
        assert "confidence_bands" in result["semantic_resolution"]

    def test_has_bayesian_inference_conventions(self):
        result = _get_conventions()
        assert "bayesian_inference" in result
        assert "minimum_data" in result["bayesian_inference"]

    def test_has_causal_inference_conventions(self):
        result = _get_conventions()
        assert "causal_inference" in result
        causal = result["causal_inference"]
        assert "assumptions" in causal
        assert "caveat_codes" in causal
        assert "minimum_data" in causal
        assert "minimum_segment_windows" in causal["minimum_data"]
        assert "segment_insufficient_samples" in causal["caveat_codes"]

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


# --- TestAgentBehavior ---


class TestAgentBehavior:
    def test_has_vision_and_operational(self):
        result = _get_agent_behavior()
        assert "vision" in result
        assert "operational" in result

    def test_vision_has_principles(self):
        result = _get_agent_behavior()
        principles = result["vision"]["principles"]
        assert isinstance(principles, list)
        assert len(principles) >= 3

    def test_operational_has_scope(self):
        result = _get_agent_behavior()
        scope = result["operational"]["scope"]
        assert "default" in scope
        assert "levels" in scope
        assert scope["default"] in scope["levels"]

    def test_operational_has_rules(self):
        result = _get_agent_behavior()
        rules = result["operational"]["rules"]
        assert isinstance(rules, list)
        assert len(rules) >= 3
