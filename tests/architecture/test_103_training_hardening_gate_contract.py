from __future__ import annotations

from kura_workers.system_config import _get_conventions
from kura_workers.training_hardening_gate_v1 import evaluate_hardening_gate_v1


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


def test_training_hardening_gate_contract_is_declared() -> None:
    conventions = _get_conventions()
    contract = conventions["training_hardening_gate_v1"]["contract"]
    assert contract["schema_version"] == "training_hardening_gate.v1"
    assert "load_calibration" in contract["tracks"]
    assert "kura-training-316.15" in contract["required_issue_chain"]


def test_training_hardening_gate_returns_binary_rollout_decision() -> None:
    passing = evaluate_hardening_gate_v1(_passing_snapshot())
    assert passing["status"] == "pass"
    assert passing["allow_rollout"] is True

    failing_input = _passing_snapshot()
    failing_input["import_mapping"]["golden_fixture_pass_rate_pct"] = 75.0
    failing = evaluate_hardening_gate_v1(failing_input)
    assert failing["status"] == "fail"
    assert failing["allow_rollout"] is False
    assert failing["failed_checks"]
