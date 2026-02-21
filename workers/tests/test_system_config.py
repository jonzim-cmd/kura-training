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
        assert "section_metadata" in result

    def test_time_conventions(self):
        result = build_system_config()
        assert result["time_conventions"]["week"] == "ISO 8601 (2026-W06)"
        assert result["time_conventions"]["date"] == "ISO 8601 (2026-02-08)"

    def test_event_conventions_count(self):
        result = build_system_config()
        assert len(result["event_conventions"]) >= 25

    def test_set_corrected_convention_requires_idempotency_key(self):
        result = build_system_config()
        convention = result["event_conventions"]["set.corrected"]
        assert "target_event_id" in convention["fields"]
        assert "changed_fields" in convention["fields"]
        assert "metadata_fields" in convention
        assert "idempotency_key" in convention["metadata_fields"]

    def test_session_completed_convention_declared(self):
        result = build_system_config()
        convention = result["event_conventions"]["session.completed"]
        assert "enjoyment" in convention["fields"]
        assert "perceived_quality" in convention["fields"]
        assert "metadata_fields" in convention
        assert "session_id" in convention["metadata_fields"]

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

    def test_section_metadata_contains_core_and_nested_sections(self):
        result = build_system_config()
        metadata = result["section_metadata"]
        assert metadata["schema_version"] == "system_config_section_metadata.v1"
        sections = metadata["sections"]
        assert sections["system_config"]["criticality"] == "core"
        assert sections["system_config.operational_model"]["criticality"] == "core"
        assert "Event Sourcing" in sections["system_config.operational_model"]["purpose"]
        assert (
            sections["system_config.event_conventions::set.logged"]["purpose"]
            == "Formal event schema contract for writes and corrections."
        )
        assert sections["system_config.section_metadata"]["criticality"] == "extended"


# --- TestConventions ---


class TestConventions:
    def test_has_first_contact_opening_v1(self):
        result = _get_conventions()
        assert "first_contact_opening_v1" in result
        opening = result["first_contact_opening_v1"]
        assert opening["schema_version"] == "first_contact_opening.v1"
        assert opening["required_sequence"] == [
            "what_kura_is",
            "how_to_use",
            "onboarding_interview_offer",
        ]
        assert opening["interview_offer"]["required"] is True
        assert opening["interview_offer"]["format"] == "offer_onboarding_fork_quick_or_deep"
        assert opening["interview_offer"]["default_path"] == "deep"
        assert opening["interview_offer"]["recommended_path"] == "deep"
        assert "Kura is a structured training-data system." in opening["mandatory_sentence"]

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

    def test_has_training_core_fields_v1(self):
        result = _get_conventions()
        assert "training_core_fields_v1" in result
        registry = result["training_core_fields_v1"]["modality_registry"]
        assert "strength" in registry
        assert "mention_bound" in registry["strength"]
        assert "rest_seconds" in registry["strength"]["mention_bound"]

    def test_has_training_session_block_model_v1(self):
        result = _get_conventions()
        assert "training_session_block_model_v1" in result
        contract = result["training_session_block_model_v1"]
        assert contract["event_type"] == "session.logged"
        assert contract["contract"]["contract_version"] == "session.logged.v1"
        assert "strength_set" in contract["contract"]["block_types"]
        assert contract["contract"]["intensity_policy"]["global_hr_requirement"] is False
        assert set(contract["completeness_policy"]["levels"]) == {
            "log_valid",
            "analysis_basic",
            "analysis_advanced",
        }
        assert contract["completeness_policy"]["global_requirements"]["heart_rate_required"] is False
        error_contract = contract["completeness_policy"]["error_contract"]
        assert error_contract["schema_version"] == "session.completeness.errors.v1"
        assert "session.logged.anchor.missing" in error_contract["codes"]
        rules_text = " ".join(contract["rules"]).lower()
        assert "block-relevant and minimal" in rules_text

    def test_has_load_context_v1_conventions(self):
        result = _get_conventions()
        assert "load_context_v1" in result
        load_context = result["load_context_v1"]
        assert load_context["event_type"] == "set.logged"
        assert "load_context.comparability_group" in load_context["required_fields_when_present"]

    def test_has_training_load_projection_v2_conventions(self):
        result = _get_conventions()
        assert "training_load_projection_v2" in result
        projection = result["training_load_projection_v2"]
        assert projection["projection_type"] == "training_timeline"
        assert projection["contract"]["schema_version"] == "training_load.v2"
        assert "strength" in projection["contract"]["modalities"]

    def test_has_training_load_calibration_v1_conventions(self):
        result = _get_conventions()
        assert "training_load_calibration_v1" in result
        calibration = result["training_load_calibration_v1"]["contract"]
        assert calibration["schema_version"] == "training_load_calibration.v1"
        assert "brier_score" in calibration["metrics"]
        assert "baseline_v1" in calibration["parameter_registry"]["available_versions"]

    def test_has_session_legacy_compatibility_v1_conventions(self):
        result = _get_conventions()
        assert "session_legacy_compatibility_v1" in result
        contract = result["session_legacy_compatibility_v1"]["contract"]
        assert contract["migration_strategy"]["replay_safe"] is True
        assert contract["coexistence_policy"]["allow_parallel_event_types"] is True

    def test_has_training_rollout_guard_v1_conventions(self):
        result = _get_conventions()
        assert "training_rollout_guard_v1" in result
        contract = result["training_rollout_guard_v1"]["contract"]
        assert "strength_manual_only" in contract["qa_matrix"]
        assert "training_load_v2" in contract["feature_flags"]
        assert "external_import_parse_fail_rate_pct" in contract["monitoring"]["metrics"]
        assert contract["hardening_gate"]["schema_version"] == "training_hardening_gate.v1"

    def test_has_training_hardening_gate_v1_conventions(self):
        result = _get_conventions()
        assert "training_hardening_gate_v1" in result
        contract = result["training_hardening_gate_v1"]["contract"]
        assert contract["schema_version"] == "training_hardening_gate.v1"
        assert "error_taxonomy" in contract["tracks"]

    def test_has_external_import_mapping_v2_conventions(self):
        result = _get_conventions()
        assert "external_import_mapping_v2" in result
        contract = result["external_import_mapping_v2"]["contract"]
        assert contract["schema_version"] == "external_import_mapping.v2"
        assert "garmin" in contract["provider_field_matrix"]
        assert "fit" in contract["format_field_matrix"]
        assert "running" in contract["modalities"]
        assert "provider_modality_matrix" in contract

    def test_has_external_import_error_taxonomy_v1_conventions(self):
        result = _get_conventions()
        assert "external_import_error_taxonomy_v1" in result
        contract = result["external_import_error_taxonomy_v1"]["contract"]
        assert contract["schema_version"] == "external_import_error_taxonomy.v1"
        assert set(contract["classes"]) == {
            "parse",
            "mapping",
            "validation",
            "dedup",
            "other",
        }
        assert set(contract["parse_quality_classes"]) == {
            "parse",
            "mapping",
            "validation",
        }

    def test_has_session_feedback_certainty_v1_conventions(self):
        result = _get_conventions()
        assert "session_feedback_certainty_v1" in result
        certainty = result["session_feedback_certainty_v1"]
        assert certainty["event_type"] == "session.completed"
        assert "enjoyment" in certainty["covered_fields"]

    def test_has_schema_capability_gate_v1_conventions(self):
        result = _get_conventions()
        assert "schema_capability_gate_v1" in result
        gate = result["schema_capability_gate_v1"]
        assert gate["required_relation_checks"][0]["relation"] == "external_import_jobs"

    def test_has_evidence_layer_v1_conventions(self):
        result = _get_conventions()
        assert "evidence_layer_v1" in result
        evidence = result["evidence_layer_v1"]
        assert evidence["parser_version"] == "mention_parser.v1"
        assert evidence["event_type"] == "evidence.claim.logged"
        assert "provenance.source_text_span" in evidence["required_fields"]

    def test_has_open_observation_v1_conventions(self):
        result = _get_conventions()
        assert "open_observation_v1" in result
        observation = result["open_observation_v1"]
        assert observation["event_type"] == "observation.logged"
        assert observation["projection_type"] == "open_observations"
        assert "motivation_pre" in observation["validation_tiers"]["known"]

    def test_open_observation_v1_declares_lifecycle_policy(self):
        result = _get_conventions()
        lifecycle = result["open_observation_v1"]["lifecycle_policy"]
        assert "known" in lifecycle["states"]
        assert lifecycle["thresholds"]["promotion_min_support"] >= 1
        assert lifecycle["thresholds"]["promotion_min_avg_confidence"] <= 1.0

    def test_has_ingestion_locale_v1_conventions(self):
        result = _get_conventions()
        assert "ingestion_locale_v1" in result
        locale = result["ingestion_locale_v1"]
        assert "numeric_normalization" in locale
        assert "decimal_comma_example" in locale["numeric_normalization"]
        assert locale["date_time_normalization"]["timezone_required_for_temporal_claims"] is True

    def test_has_learning_clustering_v1_conventions(self):
        result = _get_conventions()
        assert "learning_clustering_v1" in result
        clustering = result["learning_clustering_v1"]
        assert clustering["source_event_type"] == "learning.signal.logged"
        assert clustering["output_table"] == "learning_issue_clusters"
        assert set(clustering["period_granularities"]) == {"day", "week"}

    def test_learning_clustering_v1_false_positive_controls_are_declared(self):
        result = _get_conventions()
        controls = result["learning_clustering_v1"]["false_positive_controls"]
        assert controls["min_support_default"] >= 1
        assert controls["min_unique_users_default"] >= 1
        assert "confidence_band_policy" in controls

    def test_has_extraction_calibration_v1_conventions(self):
        result = _get_conventions()
        assert "extraction_calibration_v1" in result
        calibration = result["extraction_calibration_v1"]
        assert calibration["source_event_type"] == "evidence.claim.logged"
        assert calibration["output_table"] == "extraction_calibration_metrics"
        assert "brier_score" in calibration["metrics"]

    def test_extraction_calibration_v1_policy_integration_contract(self):
        result = _get_conventions()
        integration = result["extraction_calibration_v1"]["policy_integration"]
        assert integration["autonomy_policy_field"] == "calibration_status"
        assert integration["degraded_effect"] == "disable_auto_repair"
        assert integration["monitor_effect"] == "throttle_auto_repair"

    def test_has_model_tier_registry_v1_conventions(self):
        result = _get_conventions()
        assert "model_tier_registry_v1" in result
        registry = result["model_tier_registry_v1"]
        identity = registry["identity_resolution"]
        assert identity["identity_purpose"] == "audit_and_quality_track_separation"
        assert identity["identity_does_not_affect_tier"] is True
        assert "model_attestation (HMAC-verified runtime identity)" in identity["trusted_sources_order"]
        assert registry["default_start_tier"] == "moderate"
        assert set(registry["tiers"]) == {"strict", "moderate", "advanced"}

    def test_model_tier_registry_v1_tiers_are_machine_readable(self):
        result = _get_conventions()
        tiers = result["model_tier_registry_v1"]["tiers"]
        assert tiers["strict"]["high_impact_write_policy"] == "confirm_first"
        assert tiers["strict"]["intent_handshake_required"] is True
        assert tiers["moderate"]["high_impact_write_policy"] == "confirm_first"
        assert tiers["advanced"]["high_impact_write_policy"] == "allow"
        assert tiers["advanced"]["intent_handshake_required"] is False
        assert "reason_codes" in result["model_tier_registry_v1"]["policy_outputs"]

    def test_has_learning_backlog_bridge_v1_conventions(self):
        result = _get_conventions()
        assert "learning_backlog_bridge_v1" in result
        bridge = result["learning_backlog_bridge_v1"]
        assert bridge["output_table"] == "learning_backlog_candidates"
        assert bridge["run_table"] == "learning_backlog_bridge_runs"
        assert "learning_issue_clusters" in bridge["source_tables"]
        assert "extraction_underperforming_classes" in bridge["source_tables"]
        assert "unknown_dimension" in bridge["candidate_payload_contract"]["source_type_values"]

    def test_learning_backlog_bridge_v1_declares_promotion_workflow_and_guardrails(self):
        result = _get_conventions()
        bridge = result["learning_backlog_bridge_v1"]
        assert "promotion_workflow" in bridge
        assert "shadow_re_evaluation" in bridge["promotion_workflow"]
        assert bridge["candidate_payload_contract"]["approval_required_default"] is True
        assert bridge["guardrails"]["cluster_min_score_default"] > 0.0
        assert bridge["guardrails"]["max_candidates_per_run_default"] >= 1

    def test_has_unknown_dimension_mining_v1_conventions(self):
        result = _get_conventions()
        assert "unknown_dimension_mining_v1" in result
        mining = result["unknown_dimension_mining_v1"]
        assert mining["source_event_type"] == "observation.logged"
        assert mining["output_table"] == "unknown_dimension_proposals"
        assert mining["run_table"] == "unknown_dimension_mining_runs"
        assert "name" in mining["schema_suggestion_fields"]
        assert mining["backlog_bridge_integration"]["target_source_type"] == "unknown_dimension"
        assert "human_acceptance" in mining["approval_workflow"]

    def test_has_shadow_evaluation_gate_v1_conventions(self):
        result = _get_conventions()
        assert "shadow_evaluation_gate_v1" in result
        gate = result["shadow_evaluation_gate_v1"]
        assert gate["entrypoint"] == "eval_harness.run_shadow_evaluation"
        assert gate["release_gate_policy_version"] == "shadow_eval_gate_v1"
        assert gate["tier_matrix_policy_version"] == "shadow_eval_tier_matrix_v1"
        assert gate["weakest_tier"] == "strict"
        assert "model_tiers" in gate["inputs"]["baseline_config"]
        assert "model_tiers" in gate["inputs"]["candidate_config"]
        assert "metric_deltas" in gate["report_sections"]
        assert "tier_matrix" in gate["report_sections"]
        assert "adversarial_corpus" in gate["report_sections"]
        assert "release_gate" in gate["report_sections"]
        assert len(gate["delta_rules"]) >= 2

    def test_has_synthetic_adversarial_corpus_v1_conventions(self):
        result = _get_conventions()
        assert "synthetic_adversarial_corpus_v1" in result
        corpus = result["synthetic_adversarial_corpus_v1"]
        assert corpus["schema_version"] == "synthetic_adversarial_corpus.v1"
        assert corpus["policy_role"] == "advisory_regression_gate"
        assert set(corpus["required_failure_modes"]) == {
            "hallucination",
            "overconfidence",
            "retrieval_miss",
            "data_integrity_drift",
        }
        assert corpus["entrypoint"] == "eval_harness.evaluate_synthetic_adversarial_corpus"
        assert corpus["regression_policy"]["min_sidecar_alignment_rate"] == 0.70
        sidecar = corpus["sidecar_alignment"]
        assert sidecar["retrieval_regret_signal_type"] == "retrieval_regret_observed"
        assert sidecar["laaj_signal_type"] == "laaj_sidecar_assessed"

    def test_sidecar_retrieval_regret_v1_declares_runtime_and_developer_channels(self):
        result = _get_conventions()
        assert "sidecar_retrieval_regret_v1" in result
        sidecar = result["sidecar_retrieval_regret_v1"]
        channels = sidecar["delivery_channels"]
        assert channels["runtime_context"] == "agent_write_with_proof.response.sidecar_assessment"
        assert channels["developer_telemetry"] == "events.learning.signal.logged"
        assert channels["policy_mode"] == "advisory_only"

    def test_has_decision_brief_v1_conventions(self):
        result = _get_conventions()
        assert "decision_brief_v1" in result
        brief = result["decision_brief_v1"]
        assert brief["schema_version"] == "decision_brief.v1"
        assert set(brief["required_blocks"]) == {
            "likely_true",
            "unclear",
            "high_impact_decisions",
            "recent_person_failures",
            "person_tradeoffs",
        }
        assert brief["required_output_fields"][0] == "chat_template_id"
        caps = brief["item_caps_by_mode"]
        assert caps["concise"] == 3
        assert caps["balanced_default"] == 4
        assert caps["detailed_default"] == 5
        assert caps["explicit_detail_request_max"] == 6
        assert brief["source_priority"][0] == "quality_health/overview"
        assert brief["detail_mode"]["default_mode"] == "balanced"
        assert "ausfuehrlich" in brief["detail_mode"]["explicit_request_keywords"]
        assert (
            brief["chat_context_template"]["template_id"]
            == "decision_brief.chat.context.v1"
        )
        assert (
            brief["chat_context_template"]["section_order"][0]
            == "Was ist wahrscheinlich wahr?"
        )
        assert brief["chat_context_template"]["must_include_hypothesis_rule"] is True
        assert brief["safety"]["must_not_claim_false_certainty"] is True

    def test_has_high_impact_plan_update_v1_conventions(self):
        result = _get_conventions()
        assert "high_impact_plan_update_v1" in result
        contract = result["high_impact_plan_update_v1"]
        assert contract["schema_version"] == "high_impact_plan_update.v1"
        assert (
            "training_plan.updated"
            not in contract["classification"]["always_high_impact_event_types"]
        )
        assert (
            "workflow.onboarding.restarted"
            in contract["classification"]["always_high_impact_event_types"]
        )
        thresholds = contract["classification"]["training_plan_updated_high_impact_when"][
            "thresholds_abs_gte"
        ]
        assert thresholds["volume_delta_pct"] == 15.0
        assert thresholds["intensity_delta_pct"] == 10.0
        assert thresholds["frequency_delta_per_week"] == 2.0
        assert thresholds["cycle_length_weeks_delta"] == 2.0
        assert (
            contract["safety"]["must_avoid_bureaucratic_friction_for_routine_adjustments"]
            is True
        )

    def test_has_proof_in_production_v1_conventions(self):
        result = _get_conventions()
        assert "proof_in_production_v1" in result
        proof = result["proof_in_production_v1"]
        assert proof["entrypoint"] == "eval_harness.build_proof_in_production_artifact"
        assert proof["script_entrypoint"] == "scripts/build_proof_in_production_artifact.py"
        assert proof["schema_version"] == "proof_in_production_decision_artifact.v1"
        assert "recommended_next_steps" in proof["required_sections"]
        assert "headline" in proof["stakeholder_summary_sections"]

    def test_has_visualization_policy_conventions(self):
        result = _get_conventions()
        assert "visualization_policy" in result
        policy = result["visualization_policy"]
        assert "policy_triggers" in policy
        assert "trend" in policy["policy_triggers"]
        assert "plan_vs_actual" in policy["policy_triggers"]
        assert "resolve_endpoint" in policy
        assert policy["resolve_endpoint"] == "/v1/agent/visualization/resolve"

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

    def test_operational_has_challenge_mode_contract(self):
        result = _get_agent_behavior()
        challenge_mode = result["operational"]["challenge_mode"]
        assert challenge_mode["schema_version"] == "challenge_mode.v1"
        assert challenge_mode["default"] == "auto"
        assert challenge_mode["allowed_values"] == ["auto", "on", "off"]
        assert challenge_mode["storage_contract"]["event_type"] == "preference.set"
        assert challenge_mode["storage_contract"]["key"] == "challenge_mode"
        assert challenge_mode["discoverability"]["chat_only_control"] is True
        assert "onboarding_hint" in challenge_mode["discoverability"]

    def test_operational_has_user_override_controls_contract(self):
        result = _get_agent_behavior()
        overrides = result["operational"]["user_override_controls_v1"]
        keys = overrides["keys"]

        assert overrides["storage"] == "user_profile.user.preferences via preference.set"
        assert keys["autonomy_scope"]["allowed_values"] == ["strict", "moderate", "proactive"]
        assert keys["verbosity"]["allowed_values"] == ["concise", "balanced", "detailed"]
        assert keys["confirmation_strictness"]["allowed_values"] == ["auto", "always", "never"]
        assert keys["save_confirmation_mode"]["allowed_values"] == ["auto", "always", "never"]
        assert overrides["fallback_defaults"]["autonomy_scope"] == "moderate"
        assert overrides["fallback_defaults"]["verbosity"] == "balanced"
        assert overrides["fallback_defaults"]["confirmation_strictness"] == "auto"
        assert overrides["fallback_defaults"]["save_confirmation_mode"] == "auto"

    def test_operational_has_persist_intent_policy_contract(self):
        result = _get_agent_behavior()
        policy = result["operational"]["persist_intent_policy_v1"]
        assert policy["schema_version"] == "persist_intent_policy.v1"
        assert policy["allowed_modes"] == ["auto_save", "auto_draft", "ask_first"]
        assert policy["status_labels"] == ["saved", "draft", "not_saved"]
        assert policy["anti_spam"]["max_save_confirmation_prompts_per_turn"] == 1
        assert policy["fail_safe"]["no_saved_wording_without_proof"] is True
        assert policy["lifecycle"]["states"] == ["saved", "draft", "not_saved"]
        assert policy["lifecycle"]["review_loop_required_when_drafts_open"] is True

    def test_operational_has_observation_draft_context_contract(self):
        result = _get_agent_behavior()
        contract = result["operational"]["observation_draft_context_v1"]
        assert contract["schema_version"] == "observation_draft_context.v1"
        source = contract["source_contract"]
        assert source["event_type"] == "observation.logged"
        assert source["dimension_prefix"] == "provisional.persist_intent."
        assert source["projection_type"] == "open_observations"
        assert contract["context_fields"] == [
            "open_count",
            "oldest_draft_age_hours",
            "review_status",
            "review_loop_required",
            "next_action_hint",
            "recent_drafts[]",
        ]
        assert contract["review_status_levels"] == ["healthy", "monitor", "degraded"]

    def test_operational_has_observation_draft_promotion_contract(self):
        result = _get_agent_behavior()
        contract = result["operational"]["observation_draft_promotion_v1"]
        assert contract["schema_version"] == "observation_draft_promote.v1"
        api_contract = contract["api_contract"]
        assert api_contract["list_endpoint"] == "GET /v1/agent/observation-drafts"
        assert api_contract["detail_endpoint"] == "GET /v1/agent/observation-drafts/{observation_id}"
        assert api_contract["promote_endpoint"] == (
            "POST /v1/agent/observation-drafts/{observation_id}/promote"
        )
        guards = contract["promote_write_guards"]
        assert guards["enforce_legacy_domain_invariants"] is True
        assert guards["atomic_formal_write_plus_retract"] is True

    def test_operational_has_observation_draft_resolution_contract(self):
        result = _get_agent_behavior()
        contract = result["operational"]["observation_draft_resolution_v1"]
        assert contract["schema_version"] == "observation_draft_resolve.v1"
        api_contract = contract["api_contract"]
        assert api_contract["resolve_endpoint"] == (
            "POST /v1/agent/observation-drafts/{observation_id}/resolve-as-observation"
        )
        guards = contract["resolve_write_guards"]
        assert guards["requires_non_provisional_dimension"] is True
        assert guards["event_type"] == "observation.logged"
        assert guards["atomic_observation_write_plus_retract"] is True
        assert guards["default_retract_reason"] == "resolved_as_observation"

    def test_operational_has_draft_hygiene_feedback_contract(self):
        result = _get_agent_behavior()
        contract = result["operational"]["draft_hygiene_feedback_v1"]
        assert contract["schema_version"] == "draft_hygiene_feedback.v1"
        assert contract["status_levels"] == ["healthy", "monitor", "degraded"]
        assert contract["window_days"] == 7
        assert "draft_hygiene.backlog_open" in contract["quality_health_fields"]

    def test_operational_has_scenario_library_with_required_categories(self):
        result = _get_agent_behavior()
        library = result["operational"]["scenario_library_v1"]
        scenarios = library["scenarios"]

        assert set(library["required_categories"]) == {
            "happy_path",
            "ambiguity",
            "correction",
            "contradiction",
            "low_confidence",
            "overload",
            "consistency_prompt",
        }
        assert len(scenarios) >= 7
        for scenario in scenarios:
            assert scenario["id"]
            assert scenario["category"] in library["required_categories"]
            assert "expected_machine_outputs" in scenario
            assert "expected_user_phrasing" in scenario
            phrasing = scenario["expected_user_phrasing"]
            assert phrasing["label"] in {"Saved", "Inferred", "Unresolved", "Approval-Frage"}
            assert phrasing["clarification_strategy"]

    def test_operational_has_write_protocol(self):
        result = _get_agent_behavior()
        write_protocol = result["operational"]["write_protocol"]
        assert "required_steps" in write_protocol
        assert "saved_claim_policy" in write_protocol
        assert len(write_protocol["required_steps"]) >= 3

    def test_operational_has_reliability_ux_protocol_with_three_states(self):
        result = _get_agent_behavior()
        protocol = result["operational"]["reliability_ux_protocol"]
        states = protocol["state_contract"]

        assert set(states.keys()) == {"saved", "inferred", "unresolved"}
        assert "must_include" in states["saved"]
        assert "must_include" in states["inferred"]
        assert "must_include" in states["unresolved"]
        assert "inferred_facts[]" in states["inferred"]["must_include"]
        assert "clarification_question" in states["unresolved"]["must_include"]

    def test_reliability_ux_protocol_preserves_override_hooks(self):
        result = _get_agent_behavior()
        compatibility = result["operational"]["reliability_ux_protocol"]["compatibility"]
        hooks = compatibility["hooks"]

        assert compatibility["user_override_hooks_must_remain_supported"] is True
        assert "workflow_gate.override" in hooks
        assert "autonomy_policy.max_scope_level" in hooks
        assert "confirmation_template_catalog" in hooks

    def test_operational_has_uncertainty_markers(self):
        result = _get_agent_behavior()
        uncertainty = result["operational"]["uncertainty"]
        assert "required_markers" in uncertainty
        markers = uncertainty["required_markers"]
        assert "uncertain" in markers
        assert "deferred" in markers

    def test_operational_has_autonomy_confirmation_templates(self):
        result = _get_agent_behavior()
        throttling = result["operational"]["autonomy_throttling"]
        catalog = throttling["confirmation_template_catalog"]
        assert "healthy" in catalog
        assert "monitor" in catalog
        assert "degraded" in catalog
        assert "non_trivial_action" in catalog["degraded"]

    def test_operational_has_security_tiering_profiles(self):
        result = _get_agent_behavior()
        security_tiering = result["operational"]["security_tiering"]

        assert security_tiering["default_profile"] == "default"
        assert security_tiering["profile_progression"] == [
            "default",
            "adaptive",
            "strict",
        ]
        assert set(security_tiering["profiles"]) == {"default", "adaptive", "strict"}

    def test_security_tiering_profiles_reference_known_switches(self):
        result = _get_agent_behavior()
        security_tiering = result["operational"]["security_tiering"]
        switch_catalog = set(security_tiering["switch_catalog"])

        for profile in security_tiering["profiles"].values():
            switches = profile["switches"]
            assert set(switches).issubset(switch_catalog)
            assert switches["prompt_hardening"]
            assert switches["scope_enforcement"]

    def test_security_tiering_switch_catalog_has_owner_metric_rollout(self):
        result = _get_agent_behavior()
        security_tiering = result["operational"]["security_tiering"]

        for control in security_tiering["switch_catalog"].values():
            assert control["owner"]
            assert control["metric"]
            assert control["rollout_plan"]

    def test_security_tiering_threat_matrix_has_required_fields(self):
        result = _get_agent_behavior()
        security_tiering = result["operational"]["security_tiering"]
        threat_matrix = security_tiering["threat_matrix"]

        assert len(threat_matrix) >= 4
        names = {entry["name"] for entry in threat_matrix}
        assert "prompt_exfiltration" in names
        assert "api_enumeration" in names
        assert "context_scraping" in names
        assert "scope_escalation" in names

        for entry in threat_matrix:
            assert entry["threat_id"].startswith("TM-")
            assert entry["attacker_goal"]
            assert entry["attack_path"]
            assert entry["detection_signals"]
            assert entry["controls"]["default"]
            assert entry["controls"]["adaptive"]
            assert entry["controls"]["strict"]
            assert entry["owner"]
            assert entry["metric"]
            assert entry["rollout_plan"]
