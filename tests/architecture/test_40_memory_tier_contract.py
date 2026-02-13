from __future__ import annotations

from tests.architecture.conftest import assert_kura_api_test_passes


def test_memory_tier_contract_keeps_allow_when_principles_are_fresh() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::memory_tier_contract_keeps_allow_when_principles_are_fresh"
    )


def test_memory_tier_contract_requires_confirmation_when_principles_missing() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::memory_tier_contract_requires_confirmation_when_principles_missing"
    )
