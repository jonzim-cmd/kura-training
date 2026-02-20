from __future__ import annotations

from pathlib import Path

from kura_workers.causal_estimand_registry_v2 import (
    build_estimand_identity_v2,
    causal_estimand_registry_v2,
    resolve_estimand_spec_v2,
)
from kura_workers.system_config import _get_conventions

CAUSAL_HANDLER = Path("workers/src/kura_workers/handlers/causal_inference.py")


def test_estimand_registry_v2_is_declared_in_system_conventions() -> None:
    conventions = _get_conventions()
    block = conventions["causal_estimand_registry_v2"]
    assert block["contract"]["schema_version"] == "causal_estimand_registry.v2"


def test_estimand_registry_v2_pins_identity_and_confounder_contract() -> None:
    registry = causal_estimand_registry_v2()
    assert registry["identity_dimensions"] == [
        "intervention",
        "outcome",
        "objective_mode",
        "modality",
        "exercise_id",
    ]
    interventions = registry["interventions"]
    assert "program_change" in interventions
    assert "readiness_score_t_plus_1" in interventions["program_change"]
    assert interventions["program_change"]["readiness_score_t_plus_1"]["confounders"]


def test_estimand_registry_v2_resolver_and_identity_builder_are_open_set_safe() -> None:
    identity = build_estimand_identity_v2(
        intervention="Program_Change",
        outcome="Readiness_Score_T_Plus_1",
        objective_mode="coach",
        modality="running",
        exercise_id=None,
    )
    assert identity["intervention"] == "program_change"
    assert identity["outcome"] == "readiness_score_t_plus_1"
    assert identity["objective_mode"] == "coach"
    assert identity["modality"] == "running"

    fallback = resolve_estimand_spec_v2("unknown_intervention", "unknown_outcome")
    assert fallback["estimand_type"] == "average_treatment_effect"
    assert fallback["confounders"]
    assert "overlap_floor" in fallback["required_diagnostics"]


def test_causal_handler_references_estimand_registry_v2_surface() -> None:
    src = CAUSAL_HANDLER.read_text(encoding="utf-8")
    assert "resolve_estimand_spec_v2" in src
    assert "build_estimand_identity_v2" in src
    assert "estimand_identity" in src
