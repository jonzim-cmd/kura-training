from __future__ import annotations

from kura_workers.event_conventions import get_event_conventions
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::personal_failure_profile_contract_is_deterministic_per_user_and_model",
    "routes::agent::tests::personal_failure_profile_contract_is_advisory_not_cage",
    "routes::agent::tests::personal_failure_profile_contract_tracks_active_failure_signals",
)


def test_personal_failure_profile_contract_declares_schema_keying_and_advisory_role() -> None:
    contract = _get_conventions()["personal_failure_profile_v1"]
    assert contract["schema_version"] == "personal_failure_profile.v1"
    assert contract["policy_role"] == "advisory_only"
    assert "profile_id_seed" in contract["keying"]
    assert {
        "profile_id",
        "model_identity",
        "data_quality_band",
        "recommended_response_mode",
        "active_signals[]",
    } <= set(contract["required_fields"])


def test_personal_failure_profile_signal_is_documented_for_learning_events() -> None:
    signal_field = get_event_conventions()["learning.signal.logged"]["fields"]["signal_type"]
    assert "personal_failure_profile_observed" in signal_field


def test_personal_failure_profile_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
