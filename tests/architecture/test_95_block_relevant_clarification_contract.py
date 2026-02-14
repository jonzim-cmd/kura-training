from __future__ import annotations

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


SESSION_CLARIFICATION_RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::session_audit_session_logged_strength_without_hr_keeps_clean",
    "routes::agent::tests::session_audit_session_logged_interval_missing_anchor_requires_block_question",
    "routes::agent::tests::session_audit_session_logged_not_applicable_anchor_status_keeps_clean",
)


def test_training_session_rules_require_block_relevant_clarifications() -> None:
    conventions = _get_conventions()
    rules = conventions["training_session_block_model_v1"]["rules"]
    rules_text = " ".join(rules).lower()

    assert "block-specific" in rules_text
    assert "no global hr requirement" in rules_text
    assert "clarifications must be block-relevant and minimal" in rules_text


def test_session_logged_clarification_runtime_cases_pass() -> None:
    for test_name in SESSION_CLARIFICATION_RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
