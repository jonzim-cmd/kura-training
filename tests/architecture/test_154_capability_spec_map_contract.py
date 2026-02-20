from __future__ import annotations

from kura_workers.handlers.capability_estimation import (
    CAPABILITY_ENVELOPE_BUILDERS,
    CAPABILITY_SPECS,
)


def test_capability_spec_map_covers_all_supported_capabilities() -> None:
    expected = {"strength_1rm", "sprint_max_speed", "jump_height", "endurance_threshold"}
    assert set(CAPABILITY_SPECS.keys()) == expected
    assert set(CAPABILITY_ENVELOPE_BUILDERS.keys()) == expected


def test_capability_specs_pin_observation_policy_and_instructions() -> None:
    for spec in CAPABILITY_SPECS.values():
        assert spec.required_observations >= spec.minimum_observations >= 1
        assert spec.insufficient_recommendations
        assert spec.comparability_fields
        assert spec.comparability_fallback
