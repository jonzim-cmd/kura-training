from __future__ import annotations

from tests.architecture.conftest import assert_kura_api_test_passes


REQUIRED_AGENT_CONTRACT_MATRIX: dict[str, dict[str, str]] = {
    "intent_handshake": {
        "schema_pin": "routes::agent::tests::intent_handshake_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::intent_handshake_contract_accepts_fresh_matching_payload",
        "negative_case": "routes::agent::tests::intent_handshake_contract_rejects_stale_payload",
    },
    "trace_digest": {
        "schema_pin": "routes::agent::tests::trace_digest_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::trace_digest_contract_is_deterministic_when_verification_is_complete",
        "negative_case": "routes::agent::tests::trace_digest_contract_marks_pending_verification_and_unsaved_claim",
    },
    "memory_tier_contract": {
        "schema_pin": "routes::agent::tests::memory_tier_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::memory_tier_contract_keeps_allow_when_principles_are_fresh",
        "negative_case": "routes::agent::tests::memory_tier_contract_requires_confirmation_when_principles_missing",
    },
    "post_task_reflection": {
        "schema_pin": "routes::agent::tests::post_task_reflection_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::post_task_reflection_contract_confirms_when_verification_and_audit_are_clean",
        "negative_case": "routes::agent::tests::post_task_reflection_contract_marks_unresolved_when_verification_fails",
    },
    "save_echo_policy": {
        "schema_pin": "routes::agent::tests::save_echo_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::save_echo_contract_enforced_in_moderate_tier",
        "negative_case": "routes::agent::tests::save_echo_contract_not_required_when_claim_failed",
    },
    "save_claim_mismatch_severity": {
        "schema_pin": "routes::agent::tests::save_claim_mismatch_severity_contract_critical_when_echo_missing",
        "positive_case": "routes::agent::tests::save_claim_mismatch_severity_contract_info_when_only_protocol_detail_missing",
        "negative_case": "routes::agent::tests::save_claim_mismatch_severity_contract_backcompat_defaults_for_legacy_payload",
    },
    "consistency_inbox": {
        "schema_pin": "routes::agent::tests::consistency_inbox_contract_is_exposed_in_context",
        "positive_case": "routes::agent::tests::consistency_inbox_contract_requires_explicit_approval_before_fix",
        "negative_case": "routes::agent::tests::consistency_inbox_contract_respects_snooze_cooldown",
    },
    "response_mode_policy": {
        "schema_pin": "routes::agent::tests::response_mode_policy_contract_prefers_grounded_when_proof_verified",
        "positive_case": "routes::agent::tests::response_mode_policy_contract_uses_hypothesis_when_evidence_is_partial",
        "negative_case": "routes::agent::tests::response_mode_policy_contract_falls_back_to_general_without_evidence",
    },
    "personal_failure_profile": {
        "schema_pin": "routes::agent::tests::personal_failure_profile_contract_is_deterministic_per_user_and_model",
        "positive_case": "routes::agent::tests::personal_failure_profile_contract_tracks_active_failure_signals",
        "negative_case": "routes::agent::tests::personal_failure_profile_contract_is_advisory_not_cage",
    },
    "sidecar_retrieval_regret": {
        "schema_pin": "routes::agent::tests::sidecar_laa_j_contract_is_advisory_only_and_cannot_block",
        "positive_case": "routes::agent::tests::sidecar_signal_contract_emits_retrieval_and_laaj_signal_types",
        "negative_case": "routes::agent::tests::sidecar_retrieval_regret_contract_sets_high_regret_when_readback_incomplete",
    },
    "counterfactual_recommendation": {
        "schema_pin": "routes::agent::tests::counterfactual_recommendation_contract_is_advisory_and_transparent",
        "positive_case": "routes::agent::tests::counterfactual_recommendation_signal_contract_emits_quality_signal",
        "negative_case": "routes::agent::tests::counterfactual_recommendation_contract_keeps_ux_compact",
    },
    "policy_kernel": {
        "schema_pin": "routes::agent::tests::policy_kernel_contract_keeps_response_mode_threshold_defaults_in_sync_with_conventions",
        "positive_case": "routes::agent::tests::policy_kernel_contract_matches_reference_legacy_calculation_for_risky_case",
        "negative_case": "routes::agent::tests::policy_kernel_contract_keeps_sidecar_and_counterfactual_advisory",
    },
    "decision_brief": {
        "schema_pin": "routes::agent::tests::decision_brief_contract_exposes_required_blocks",
        "positive_case": "routes::agent::tests::decision_brief_contract_highlights_high_impact_decisions_from_consistency_inbox",
        "negative_case": "routes::agent::tests::decision_brief_contract_uses_person_tradeoffs_from_preferences",
    },
    "high_impact_plan_update": {
        "schema_pin": "routes::agent::tests::high_impact_classification_keeps_routine_plan_update_low_impact",
        "positive_case": "routes::agent::tests::high_impact_classification_escalates_large_plan_shift",
        "negative_case": "routes::agent::tests::high_impact_classification_keeps_routine_plan_update_low_impact",
    },
    "temporal_grounding": {
        "schema_pin": "routes::agent::tests::temporal_grounding_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::temporal_grounding_contract_computes_days_since_last_training",
        "negative_case": "routes::agent::tests::temporal_grounding_contract_falls_back_to_utc_when_timezone_missing",
    },
    "temporal_phrase_regression": {
        "schema_pin": "routes::agent::tests::temporal_phrase_regression_contract_covers_five_natural_language_scenarios",
        "positive_case": "routes::agent::tests::temporal_phrase_regression_contract_keeps_plus_five_hours_on_same_local_day",
        "negative_case": "routes::agent::tests::temporal_phrase_regression_contract_adjusts_day_delta_after_timezone_switch",
    },
    "persist_intent": {
        "schema_pin": "routes::agent::tests::persist_intent_contract_schema_version_is_pinned",
        "positive_case": "routes::agent::tests::persist_intent_contract_auto_save_for_verified_routine_write",
        "negative_case": "routes::agent::tests::persist_intent_contract_asks_first_for_high_impact_when_unsaved",
    },
}


def test_required_agent_contract_matrix_keys_are_explicit() -> None:
    assert set(REQUIRED_AGENT_CONTRACT_MATRIX) == {
        "intent_handshake",
        "trace_digest",
        "memory_tier_contract",
        "post_task_reflection",
        "save_echo_policy",
        "save_claim_mismatch_severity",
        "consistency_inbox",
        "response_mode_policy",
        "personal_failure_profile",
        "sidecar_retrieval_regret",
        "counterfactual_recommendation",
        "policy_kernel",
        "decision_brief",
        "high_impact_plan_update",
        "temporal_grounding",
        "temporal_phrase_regression",
        "persist_intent",
    }
    for contract_name, scenarios in REQUIRED_AGENT_CONTRACT_MATRIX.items():
        assert set(scenarios) == {"schema_pin", "positive_case", "negative_case"}, contract_name


def test_required_agent_contract_matrix_runtime_checks_pass() -> None:
    for scenarios in REQUIRED_AGENT_CONTRACT_MATRIX.values():
        for test_name in scenarios.values():
            assert test_name.startswith("routes::agent::tests::")
            assert_kura_api_test_passes(test_name)
