from __future__ import annotations

from pathlib import Path

from kura_workers.handlers.objective_advisory import _warning
from kura_workers.objective_advisory_v1 import objective_advisory_contract_v1

HANDLER = Path("workers/src/kura_workers/handlers/objective_advisory.py")


def test_objective_advisory_contract_is_advisory_only() -> None:
    contract = objective_advisory_contract_v1()
    assert contract["schema_version"] == "objective_advisory.v1"
    assert contract["policy_role"] == "advisory_only"
    assert contract["override_policy"]["warnings_overridable"] is True


def test_objective_advisory_warning_payload_is_overridable() -> None:
    warning = _warning(
        code="objective_trackability_gap",
        severity="warning",
        confidence=0.81,
        message="Trackability gap",
        evidence={"gaps": ["missing_success_metrics"]},
    )
    assert warning["overridable"] is True
    assert warning["severity"] in {"warning", "info"}


def test_objective_advisory_surface_keeps_safety_invariants_non_overridable() -> None:
    src = HANDLER.read_text(encoding="utf-8")
    assert "safety_invariants_non_overridable" in src
    assert "consent_write_gate" in src
    assert "approval_required_high_impact_write" in src
