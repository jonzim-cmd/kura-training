from __future__ import annotations

from kura_workers.capability_estimation_v1 import capability_estimation_contract_v1
from kura_workers.system_config import _get_conventions


def test_capability_estimation_contract_is_declared_in_system_conventions() -> None:
    conventions = _get_conventions()
    contract_block = conventions["capability_estimation_v1"]
    assert "contract" in contract_block
    assert contract_block["contract"]["schema_version"] == "capability_estimation.v1"


def test_capability_estimation_contract_has_three_layer_pipeline() -> None:
    contract = capability_estimation_contract_v1()
    pipeline = contract["pipeline"]
    assert set(pipeline.keys()) == {"observation_model", "state_model", "output_contract"}
    assert pipeline["output_contract"]["agent_surface"] == "machine_readable_only"


def test_capability_registry_covers_strength_sprint_jump_endurance() -> None:
    contract = capability_estimation_contract_v1()
    registry = contract["capability_registry"]
    expected = {
        "strength_1rm",
        "sprint_max_speed",
        "jump_height",
        "endurance_threshold",
    }
    assert expected <= set(registry.keys())
    for capability in expected:
        assert registry[capability]["observation_fields"]
        assert registry[capability]["protocol_required"]
        assert registry[capability]["estimator_tiers"][-1] == "latent_state"


def test_insufficient_data_policy_requires_machine_readable_disclosure() -> None:
    contract = capability_estimation_contract_v1()
    policy = contract["minimum_data_policy"]
    assert "insufficient_data" in policy["status_values"]
    required = set(policy["required_output_fields_when_insufficient"])
    assert {"status", "required_observations", "observed_observations"} <= required
    assert "uncertainty_reason_codes" in required
    assert "recommended_next_observations" in required


def test_capability_migration_order_is_deterministic_and_dependency_linked() -> None:
    contract = capability_estimation_contract_v1()
    phases = contract["migration_order"]
    phase_ids = [entry["id"] for entry in phases]
    assert phase_ids == [
        "phase_1_foundation",
        "phase_2_strength",
        "phase_3_sprint",
        "phase_4_jump",
        "phase_5_endurance",
        "phase_6_cross_capability_eval",
    ]
    for idx in range(1, len(phases)):
        assert phases[idx]["depends_on"] == [phase_ids[idx - 1]]

