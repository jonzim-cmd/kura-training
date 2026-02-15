from __future__ import annotations

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::policy_kernel_contract_keeps_response_mode_threshold_defaults_in_sync_with_conventions",
    "routes::agent::tests::policy_kernel_contract_matches_reference_legacy_calculation_for_risky_case",
    "routes::agent::tests::policy_kernel_contract_keeps_sidecar_and_counterfactual_advisory",
)


def test_policy_kernel_contract_declares_threshold_contract_surfaces() -> None:
    conventions = _get_conventions()
    response_mode = conventions["response_mode_policy_v1"]
    retrieval_regret = conventions["sidecar_retrieval_regret_v1"]["retrieval_regret"]

    assert response_mode["schema_version"] == "response_mode_policy.v1"
    assert response_mode["adaptive_thresholds"]["base"]["A_min"] == 0.72
    assert response_mode["adaptive_thresholds"]["base"]["B_min"] == 0.42
    assert response_mode["safety"]["no_autonomy_blocking_from_mode_policy"] is True
    assert retrieval_regret["schema_version"] == "retrieval_regret.v1"
    assert retrieval_regret["threshold_default"] == 0.45
    assert retrieval_regret["adaptive_thresholds"]["integrity_or_calibration_monitor"] == 0.40
    assert retrieval_regret["adaptive_thresholds"]["integrity_or_calibration_degraded"] == 0.35


def test_policy_kernel_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
