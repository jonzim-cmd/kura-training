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
}


def test_required_agent_contract_matrix_keys_are_explicit() -> None:
    assert set(REQUIRED_AGENT_CONTRACT_MATRIX) == {
        "intent_handshake",
        "trace_digest",
        "memory_tier_contract",
        "post_task_reflection",
    }
    for contract_name, scenarios in REQUIRED_AGENT_CONTRACT_MATRIX.items():
        assert set(scenarios) == {"schema_pin", "positive_case", "negative_case"}, contract_name


def test_required_agent_contract_matrix_runtime_checks_pass() -> None:
    for scenarios in REQUIRED_AGENT_CONTRACT_MATRIX.values():
        for test_name in scenarios.values():
            assert test_name.startswith("routes::agent::tests::")
            assert_kura_api_test_passes(test_name)
