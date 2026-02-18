from __future__ import annotations

from kura_workers.system_config import _get_agent_behavior
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::persist_intent_contract_schema_version_is_pinned",
    "routes::agent::tests::persist_intent_contract_auto_save_for_verified_routine_write",
    "routes::agent::tests::persist_intent_contract_asks_first_for_high_impact_when_unsaved",
    "routes::agent::tests::persist_intent_contract_uses_single_prompt_for_multiple_reasons",
)


def _persist_intent_policy() -> dict:
    operational = _get_agent_behavior()["operational"]
    assert "persist_intent_policy_v1" in operational
    return operational["persist_intent_policy_v1"]


def test_persist_intent_policy_schema_and_modes_are_pinned() -> None:
    policy = _persist_intent_policy()
    assert policy["schema_version"] == "persist_intent_policy.v1"
    assert policy["allowed_modes"] == ["auto_save", "auto_draft", "ask_first"]
    assert policy["status_labels"] == ["saved", "draft", "not_saved"]


def test_persist_intent_policy_has_anti_spam_and_fail_safe_guards() -> None:
    policy = _persist_intent_policy()
    anti_spam = policy["anti_spam"]
    assert anti_spam["max_save_confirmation_prompts_per_turn"] == 1
    assert anti_spam["must_avoid_prompt_spam_for_routine_verified_logging"] is True

    fail_safe = policy["fail_safe"]
    assert fail_safe["no_saved_wording_without_proof"] is True
    assert fail_safe["require_explicit_status_label"] is True


def test_persist_intent_policy_declares_draft_persistence_contract() -> None:
    policy = _persist_intent_policy()
    draft = policy["draft_persistence"]
    assert draft["event_type"] == "observation.logged"
    assert draft["projection_type"] == "open_observations"
    assert draft["dimension_prefix"] == "provisional.persist_intent."


def test_persist_intent_policy_declares_lifecycle_review_fields() -> None:
    policy = _persist_intent_policy()
    lifecycle = policy["lifecycle"]
    assert lifecycle["states"] == ["saved", "draft", "not_saved"]
    assert lifecycle["review_loop_required_when_drafts_open"] is True
    assert lifecycle["review_loop_fields_in_context"] == [
        "review_status",
        "review_loop_required",
        "next_action_hint",
    ]


def test_persist_intent_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
