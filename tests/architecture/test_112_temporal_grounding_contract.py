from __future__ import annotations

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::temporal_grounding_contract_schema_version_is_pinned",
    "routes::agent::tests::temporal_grounding_contract_computes_days_since_last_training",
    "routes::agent::tests::temporal_grounding_contract_falls_back_to_utc_when_timezone_missing",
    "routes::agent::tests::temporal_basis_contract_accepts_fresh_matching_payload",
    "routes::agent::tests::temporal_basis_contract_rejects_stale_payload",
)


def test_temporal_grounding_contract_declares_required_context_and_write_guards() -> None:
    contract = _get_conventions()["temporal_grounding_v1"]
    assert contract["schema_version"] == "temporal_grounding.v1"
    assert (
        "meta.temporal_context.now_utc"
        in contract["required_agent_context_fields"]
    )
    assert (
        "meta.temporal_context.today_local_date"
        in contract["required_agent_context_fields"]
    )
    assert (
        contract["intent_handshake_temporal_basis"]["schema_version"]
        == "temporal_basis.v1"
    )
    assert (
        "context_generated_at"
        in contract["intent_handshake_temporal_basis"]["required_fields"]
    )
    assert (
        contract["intent_handshake_temporal_basis"]["max_age_minutes"] == 45
    )
    assert (
        contract["safety"]["must_not_infer_time_from_old_chat_state"] is True
    )


def test_temporal_grounding_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
