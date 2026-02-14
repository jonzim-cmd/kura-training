from __future__ import annotations

from kura_workers.training_hardening_gate_v1 import (
    evaluate_hardening_gate_v1,
    hardening_gate_contract_v1,
)


def _passing_snapshot() -> dict:
    return {
        "error_taxonomy": {
            "code_based_detection_enabled": True,
            "missing_anchor_code_coverage_pct": 100.0,
        },
        "calibration": {
            "shadow": {
                "allow_rollout": True,
                "metrics": {"delta": {"brier_score": -0.01}},
            }
        },
        "import_mapping": {
            "golden_fixture_pass_rate_pct": 100.0,
            "provider_modality_coverage_pct": 80.0,
        },
        "rollback_readiness": {
            "training_load_v2_flag_disable_verified": True,
            "calibrated_profile_flag_disable_verified": True,
            "parameter_version_pin_verified": True,
        },
    }


def test_hardening_gate_contract_declares_tracks_thresholds_and_issue_chain() -> None:
    contract = hardening_gate_contract_v1()
    assert contract["schema_version"] == "training_hardening_gate.v1"
    assert set(contract["tracks"]) == {
        "error_taxonomy",
        "load_calibration",
        "import_mapping",
        "rollback_readiness",
    }
    assert "kura-training-316.10" in contract["required_issue_chain"]


def test_hardening_gate_passes_for_green_snapshot() -> None:
    result = evaluate_hardening_gate_v1(_passing_snapshot())
    assert result["schema_version"] == "training_hardening_gate.v1"
    assert result["status"] == "pass"
    assert result["allow_rollout"] is True
    assert result["failed_checks"] == []


def test_hardening_gate_fails_and_returns_next_steps() -> None:
    failing = _passing_snapshot()
    failing["error_taxonomy"]["code_based_detection_enabled"] = False
    failing["calibration"]["shadow"]["allow_rollout"] = False

    result = evaluate_hardening_gate_v1(failing)
    assert result["status"] == "fail"
    assert result["allow_rollout"] is False
    assert result["failed_checks"]
    assert result["recommended_next_steps"]
