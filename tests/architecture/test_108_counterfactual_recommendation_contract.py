from __future__ import annotations

from kura_workers.learning_telemetry import core_signal_types, signal_category
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::counterfactual_recommendation_contract_is_advisory_and_transparent",
    "routes::agent::tests::counterfactual_recommendation_contract_keeps_ux_compact",
    "routes::agent::tests::counterfactual_recommendation_signal_contract_emits_quality_signal",
)


def test_counterfactual_recommendation_contract_declares_first_principles_and_compact_ux() -> None:
    contract = _get_conventions()["counterfactual_recommendation_v1"]
    assert contract["schema_version"] == "counterfactual_recommendation.v1"
    assert contract["policy_role"] == "advisory_only"
    assert contract["rationale_style"] == "first_principles"
    assert contract["ux"]["compact"] is True
    assert contract["ux"]["max_alternatives"] == 2
    assert contract["ux"]["max_challenge_questions"] == 1
    assert set(contract["transparency_levels"]) == {
        "evidence_anchored",
        "uncertainty_explicit",
    }
    assert contract["event_contract"]["signal_type"] == "counterfactual_recommendation_prepared"


def test_counterfactual_signal_taxonomy_is_registered() -> None:
    signals = set(core_signal_types())
    assert "counterfactual_recommendation_prepared" in signals
    assert signal_category("counterfactual_recommendation_prepared") == "quality_signal"


def test_counterfactual_recommendation_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
