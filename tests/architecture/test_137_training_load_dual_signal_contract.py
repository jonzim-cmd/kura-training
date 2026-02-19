"""Training-load dual-signal contract (zjm3 relative intensity).

Architecture Decision (kura-training-6t1x / kura-training-mo6d):

Training load must combine external dose and internal response without faking
precision on sparse data. Relative-intensity references (% of athlete-specific
reference) are first-class when fresh, but stale/missing references must fall
back deterministically to sensor/subjective anchors with higher uncertainty.
Modality assignment must stay observable and avoid silent endurance bias.
"""

from __future__ import annotations

from pathlib import Path

from kura_workers.training_load_calibration_v1 import (
    FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY,
    calibration_protocol_v1,
)
from kura_workers.training_load_v2 import load_projection_contract_v2

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = REPO_ROOT / "workers" / "src" / "kura_workers" / "system_config.py"
LOAD_V2 = REPO_ROOT / "workers" / "src" / "kura_workers" / "training_load_v2.py"


def test_dual_load_contract_declares_external_plus_internal_policy() -> None:
    contract = load_projection_contract_v2()
    dual_policy = contract["dual_load_policy"]
    assert {
        "volume_kg",
        "duration_seconds",
        "distance_meters",
        "contacts",
    } <= set(dual_policy["external_dose_dimensions"])
    assert dual_policy["internal_response_resolver_order"][0] == "relative_intensity"
    assert "uncertainty uplift" in dual_policy["fallback_policy"]


def test_calibration_contract_exposes_relative_intensity_feature_flag() -> None:
    protocol = calibration_protocol_v1()
    registry = protocol["parameter_registry"]
    assert registry["relative_intensity_feature_flag"] == FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY
    assert "dual_load_policy" in registry


def test_system_config_pins_dual_load_and_stale_reference_rules() -> None:
    src = SYSTEM_CONFIG.read_text(encoding="utf-8")
    assert "Dual-load invariant" in src
    assert "Missing/stale relative-intensity references must increase uncertainty" in src


def test_load_v2_declares_modality_assignment_observability_paths() -> None:
    src = LOAD_V2.read_text(encoding="utf-8")
    assert "_MODALITY_ASSIGNMENT_SOURCES" in src
    assert "unknown_distance_exercise" in src
