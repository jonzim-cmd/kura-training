from __future__ import annotations

from pathlib import Path

from kura_workers.event_conventions import get_event_conventions
from kura_workers.objective_advisory_v1 import objective_advisory_contract_v1

OBJECTIVE_ADVISORY_HANDLER = Path("workers/src/kura_workers/handlers/objective_advisory.py")


def test_override_policy_is_advisory_and_user_controlled() -> None:
    contract = objective_advisory_contract_v1()
    override_policy = contract["override_policy"]
    assert contract["policy_role"] == "advisory_only"
    assert override_policy["warnings_overridable"] is True
    assert set(override_policy["required_fields"]) == {
        "reason",
        "scope",
        "expected_outcome",
        "review_point",
        "actor",
    }


def test_override_event_convention_declares_required_rationale_fields() -> None:
    events = get_event_conventions()
    override_event = events["advisory.override.recorded"]
    fields = override_event["fields"]
    assert "reason" in fields
    assert "scope" in fields
    assert "expected_outcome" in fields
    assert "review_point" in fields
    assert "actor" in fields


def test_handler_surface_keeps_safety_invariants_while_allowing_override() -> None:
    src = OBJECTIVE_ADVISORY_HANDLER.read_text(encoding="utf-8")
    assert "\"policy_role\": OBJECTIVE_ADVISORY_POLICY_ROLE" in src
    assert "\"safety_invariants_non_overridable\"" in src
    assert "\"overridable\": True" in src
