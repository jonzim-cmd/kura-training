from __future__ import annotations

from tests.architecture.conftest import assert_kura_api_test_passes


def test_intent_handshake_contract_accepts_fresh_matching_payload() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::intent_handshake_contract_accepts_fresh_matching_payload"
    )


def test_intent_handshake_contract_rejects_stale_payload() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::intent_handshake_contract_rejects_stale_payload"
    )
