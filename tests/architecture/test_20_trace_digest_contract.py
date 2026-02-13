from __future__ import annotations

from tests.architecture.conftest import assert_kura_api_test_passes


def test_trace_digest_contract_is_deterministic_when_verification_is_complete() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::trace_digest_contract_is_deterministic_when_verification_is_complete"
    )


def test_trace_digest_contract_marks_pending_verification_and_unsaved_claim() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::trace_digest_contract_marks_pending_verification_and_unsaved_claim"
    )
