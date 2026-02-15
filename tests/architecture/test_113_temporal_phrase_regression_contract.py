from __future__ import annotations

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::temporal_phrase_regression_contract_covers_five_natural_language_scenarios",
    "routes::agent::tests::temporal_phrase_regression_contract_keeps_plus_five_hours_on_same_local_day",
    "routes::agent::tests::temporal_phrase_regression_contract_adjusts_day_delta_after_timezone_switch",
)


def test_temporal_phrase_regression_contract_requires_real_world_scenario_classes() -> None:
    contract = _get_conventions()["temporal_grounding_v1"]
    required = set(contract["natural_language_regression_scenarios_required"])
    assert required == {
        "same_day",
        "plus_five_hour_gap",
        "day_rollover",
        "week_rollover",
        "timezone_switch",
    }


def test_temporal_phrase_regression_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
