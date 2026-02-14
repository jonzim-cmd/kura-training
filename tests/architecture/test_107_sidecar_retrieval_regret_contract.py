from __future__ import annotations

from kura_workers.learning_telemetry import core_signal_types, signal_category
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::sidecar_retrieval_regret_contract_sets_high_regret_when_readback_incomplete",
    "routes::agent::tests::sidecar_laa_j_contract_is_advisory_only_and_cannot_block",
    "routes::agent::tests::sidecar_signal_contract_emits_retrieval_and_laaj_signal_types",
    "routes::agent::tests::sidecar_retrieval_regret_contract_uses_monitor_threshold_when_quality_is_monitor",
    "routes::agent::tests::sidecar_retrieval_regret_contract_uses_degraded_threshold_when_quality_is_degraded",
)


def test_sidecar_retrieval_regret_contract_declares_advisory_only_semantics() -> None:
    contract = _get_conventions()["sidecar_retrieval_regret_v1"]
    laaj = contract["laaj_sidecar"]
    regret = contract["retrieval_regret"]
    assert laaj["schema_version"] == "laaj_sidecar.v1"
    assert laaj["policy_role"] == "advisory_only"
    assert laaj["must_not_block_autonomy"] is True
    assert regret["schema_version"] == "retrieval_regret.v1"
    assert regret["threshold_default"] == 0.45
    assert regret["adaptive_thresholds"]["integrity_or_calibration_degraded"] == 0.35
    assert regret["adaptive_thresholds"]["integrity_or_calibration_monitor"] == 0.40
    assert set(contract["event_contract"]["signal_types"]) >= {
        "retrieval_regret_observed",
        "laaj_sidecar_assessed",
    }
    channels = contract["delivery_channels"]
    assert channels["runtime_context"] == "agent_write_with_proof.response.sidecar_assessment"
    assert channels["developer_telemetry"] == "events.learning.signal.logged"
    assert channels["policy_mode"] == "advisory_only"


def test_sidecar_signal_taxonomy_is_registered() -> None:
    signals = set(core_signal_types())
    assert "retrieval_regret_observed" in signals
    assert "laaj_sidecar_assessed" in signals
    assert signal_category("retrieval_regret_observed") == "friction_signal"
    assert signal_category("laaj_sidecar_assessed") == "quality_signal"


def test_sidecar_retrieval_regret_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
