from __future__ import annotations

from kura_workers.learning_telemetry import core_signal_types, signal_category
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::advisory_scoring_layer_contract_schema_version_is_pinned",
    "routes::agent::tests::advisory_scoring_layer_contract_is_advisory_only_non_blocking",
    "routes::agent::tests::advisory_scoring_layer_contract_maps_risky_case_to_cautionary_actions",
    "routes::agent::tests::advisory_scoring_layer_contract_maps_stable_case_to_low_friction_actions",
)


def test_advisory_scoring_layer_contract_declares_four_score_surface_and_nudge_policy() -> None:
    contract = _get_conventions()["advisory_scoring_layer_v1"]

    assert contract["schema_version"] == "advisory_scoring_layer.v1"
    assert contract["action_schema_version"] == "advisory_action_plan.v1"
    assert contract["policy_role"] == "advisory_only"

    scores = contract["scores"]
    assert set(scores) == {
        "specificity_score",
        "hallucination_risk",
        "data_quality_risk",
        "confidence_score",
    }
    assert scores["specificity_score"]["direction"] == "higher_is_better"
    assert scores["hallucination_risk"]["direction"] == "higher_is_riskier"
    assert scores["data_quality_risk"]["direction"] == "higher_is_riskier"
    assert scores["confidence_score"]["direction"] == "higher_is_better"
    for score in scores.values():
        assert score["range"] == [0.0, 1.0]

    action_map = contract["action_map"]
    assert set(action_map["response_mode_hint_values"]) == {
        "grounded_personalized",
        "hypothesis_personalized",
        "general_guidance",
    }
    assert set(action_map["persist_action_values"]) == {
        "persist_now",
        "draft_preferred",
        "ask_first",
    }
    assert action_map["clarification_question_budget_max"] == 1

    safety = contract["safety"]
    assert safety["advisory_only"] is True
    assert safety["must_not_block_autonomy"] is True
    assert safety["must_reconcile_with_persist_intent"] is True
    assert safety["must_keep_saved_wording_proof_bound"] is True

    event_contract = contract["event_contract"]
    assert event_contract["event_type"] == "learning.signal.logged"
    assert event_contract["signal_type"] == "advisory_scoring_assessed"


def test_advisory_scoring_signal_taxonomy_is_registered() -> None:
    signals = set(core_signal_types())
    assert "advisory_scoring_assessed" in signals
    assert signal_category("advisory_scoring_assessed") == "quality_signal"


def test_advisory_scoring_layer_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
