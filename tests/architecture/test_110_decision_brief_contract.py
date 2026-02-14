from __future__ import annotations

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::decision_brief_contract_exposes_required_blocks",
    "routes::agent::tests::decision_brief_contract_highlights_high_impact_decisions_from_consistency_inbox",
    "routes::agent::tests::decision_brief_contract_uses_person_tradeoffs_from_preferences",
    "routes::agent::tests::decision_brief_contract_renders_chat_context_block_with_all_entries",
    "routes::agent::tests::decision_brief_contract_expands_item_cap_when_detail_is_requested",
)


def test_decision_brief_contract_declares_compact_structured_blocks() -> None:
    contract = _get_conventions()["decision_brief_v1"]
    assert contract["schema_version"] == "decision_brief.v1"
    assert set(contract["required_blocks"]) == {
        "likely_true",
        "unclear",
        "high_impact_decisions",
        "recent_person_failures",
        "person_tradeoffs",
    }
    assert set(contract["required_output_fields"]) >= {
        "chat_template_id",
        "item_cap_per_block",
        "chat_context_block",
    }
    caps = contract["item_caps_by_mode"]
    assert caps["concise"] == 3
    assert caps["balanced_default"] == 4
    assert caps["detailed_default"] == 5
    assert caps["explicit_detail_request_max"] == 6
    assert contract["source_priority"] == [
        "quality_health/overview",
        "consistency_inbox/overview",
        "user_profile/me",
    ]
    assert contract["detail_mode"]["default_mode"] == "balanced"
    assert "ausfuehrlich" in contract["detail_mode"]["explicit_request_keywords"]
    template = contract["chat_context_template"]
    assert template["template_id"] == "decision_brief.chat.context.v1"
    assert template["section_order"] == [
        "Was ist wahrscheinlich wahr?",
        "Was ist unklar?",
        "Welche Entscheidungen sind high-impact?",
        "Welche Fehler sind mir bei dieser Person zuletzt passiert?",
        "Welche Trade-offs sind fuer diese Person wichtig?",
    ]
    assert template["must_include_hypothesis_rule"] is True
    assert contract["safety"]["must_expose_uncertainty_when_signals_are_thin"] is True
    assert contract["safety"]["must_not_claim_false_certainty"] is True


def test_decision_brief_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
