from __future__ import annotations

from kura_workers.objective_statistical_contract_v1 import (
    objective_statistical_method_contract_v1,
)
from kura_workers.system_config import _get_conventions


def test_objective_statistical_contract_is_declared_in_system_conventions() -> None:
    conventions = _get_conventions()
    block = conventions["objective_statistical_method_v1"]
    assert "contract" in block
    assert block["contract"]["schema_version"] == "objective_statistical_method.v1"


def test_objective_statistical_contract_pins_stratification_axes() -> None:
    contract = objective_statistical_method_contract_v1()
    assert set(contract["stratification_axes"]) == {
        "objective_mode",
        "modality",
        "quality_band",
    }


def test_objective_statistical_contract_requires_estimand_diagnostics() -> None:
    contract = objective_statistical_method_contract_v1()
    estimand = contract["estimand_policy"]
    assert {
        "intervention",
        "outcome",
        "objective_mode",
        "modality",
    } <= set(estimand["identity_surface"])
    assert "overlap_floor" in estimand["required_diagnostics"]
    assert "confidence_interval" in estimand["required_diagnostics"]

