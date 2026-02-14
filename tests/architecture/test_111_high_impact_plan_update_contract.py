from __future__ import annotations

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::high_impact_classification_keeps_routine_plan_update_low_impact",
    "routes::agent::tests::high_impact_classification_escalates_large_plan_shift",
)


def test_high_impact_plan_update_contract_is_risk_based() -> None:
    contract = _get_conventions()["high_impact_plan_update_v1"]
    assert contract["schema_version"] == "high_impact_plan_update.v1"
    classification = contract["classification"]
    assert "training_plan.updated" not in classification["always_high_impact_event_types"]
    rules = classification["training_plan_updated_high_impact_when"]
    assert "full_rewrite" in rules["change_scope_values"]
    assert "replace_entire_plan" in rules["flags_any_true"]
    thresholds = rules["thresholds_abs_gte"]
    assert thresholds["volume_delta_pct"] == 15.0
    assert thresholds["intensity_delta_pct"] == 10.0
    assert thresholds["frequency_delta_per_week"] == 2.0
    assert thresholds["cycle_length_weeks_delta"] == 2.0
    assert (
        contract["safety"]["must_avoid_bureaucratic_friction_for_routine_adjustments"]
        is True
    )


def test_high_impact_plan_update_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
