from __future__ import annotations

from pathlib import Path

from kura_workers.eval_harness import (
    SUPPORTED_PROJECTION_TYPES,
    build_cross_capability_release_gate,
)
from kura_workers.system_config import _get_conventions

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ARTIFACTS_MIGRATION = (
    REPO_ROOT / "migrations" / "20260302000002_eval_harness_capability_estimation_artifacts.sql"
)


def test_capability_estimation_is_supported_eval_projection_type() -> None:
    assert "capability_estimation" in SUPPORTED_PROJECTION_TYPES


def test_cross_capability_release_gate_requires_all_capabilities() -> None:
    gate = build_cross_capability_release_gate(
        [
            {
                "projection_type": "capability_estimation",
                "key": "strength_1rm",
                "status": "ok",
                "metrics": {"required_fields_ok": True, "confidence": 0.8},
            }
        ]
    )
    assert gate["status"] == "insufficient_data"
    assert gate["allow_rollout"] is False
    assert any(reason.startswith("missing_capability:") for reason in gate["reasons"])


def test_cross_capability_release_gate_passes_when_all_requirements_hold() -> None:
    rows = []
    for key in ("strength_1rm", "sprint_max_speed", "jump_height", "endurance_threshold"):
        rows.append(
            {
                "projection_type": "capability_estimation",
                "key": key,
                "status": "ok",
                "metrics": {"required_fields_ok": True, "confidence": 0.72},
            }
        )
    gate = build_cross_capability_release_gate(rows)
    assert gate["status"] == "pass"
    assert gate["allow_rollout"] is True


def test_cross_capability_gate_contract_is_declared_in_system_conventions() -> None:
    conventions = _get_conventions()
    gate_block = conventions["capability_eval_gate_v1"]
    assert gate_block["contract"]["schema_version"] == "capability_eval_gate.v1"
    assert "strength_1rm" in gate_block["contract"]["required_capabilities"]


def test_eval_artifacts_projection_type_allows_capability_estimation() -> None:
    src = EVAL_ARTIFACTS_MIGRATION.read_text(encoding="utf-8")
    assert "inference_eval_artifacts_projection_type_check" in src
    assert "'capability_estimation'" in src
