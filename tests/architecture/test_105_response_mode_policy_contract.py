from __future__ import annotations

from kura_workers.learning_telemetry import signal_category
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::response_mode_policy_contract_prefers_grounded_when_proof_verified",
    "routes::agent::tests::response_mode_policy_contract_uses_hypothesis_when_evidence_is_partial",
    "routes::agent::tests::response_mode_policy_contract_falls_back_to_general_without_evidence",
    "routes::agent::tests::response_mode_policy_contract_adapts_thresholds_from_quality_health_projection",
    "routes::agent::tests::response_mode_policy_contract_tightens_thresholds_when_outcome_history_is_risky",
    "routes::agent::tests::response_mode_policy_contract_relaxes_thresholds_when_outcome_history_is_stable",
)


def test_response_mode_policy_contract_declares_ab_c_modes_and_non_blocking_role() -> None:
    contract = _get_conventions()["response_mode_policy_v1"]
    assert contract["schema_version"] == "response_mode_policy.v1"
    assert contract["policy_role"] == "nudge_only"
    assert set(contract["modes"]) == {"A", "B", "C"}
    assert contract["modes"]["A"]["name"] == "grounded_personalized"
    assert contract["modes"]["B"]["name"] == "hypothesis_personalized"
    assert contract["modes"]["C"]["name"] == "general_guidance"
    assert contract["safety"]["no_forced_personalization"] is True
    assert contract["safety"]["no_autonomy_blocking_from_mode_policy"] is True
    thresholds = contract["adaptive_thresholds"]["base"]
    assert thresholds["A_min"] == 0.72
    assert thresholds["B_min"] == 0.42
    assert "save_claim_posterior_risk" in contract["evidence_score"]["components"]
    assert "challenge_rate" in contract["evidence_score"]["components"]
    assert "follow_through_rate" in contract["evidence_score"]["components"]
    assert "retrieval_regret_exceeded_rate" in contract["evidence_score"]["components"]
    assert contract["outcome_tuning"]["sample_floor"]["response_mode_selected_total"] == 8
    assert contract["outcome_tuning"]["sample_floor"]["post_task_reflection_total"] == 8
    assert contract["outcome_tuning"]["apply_only_when_sample_ok"] is True


def test_response_mode_signal_taxonomy_is_registered() -> None:
    assert signal_category("response_mode_selected") == "outcome_signal"


def test_response_mode_policy_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
