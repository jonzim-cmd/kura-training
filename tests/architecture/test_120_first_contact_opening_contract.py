from __future__ import annotations

from pathlib import Path

from kura_workers.system_config import _get_conventions


AGENT_ROUTE = Path("api/src/routes/agent.rs")


def test_first_contact_opening_contract_is_declared_in_system_conventions() -> None:
    contract = _get_conventions()["first_contact_opening_v1"]

    assert contract["schema_version"] == "first_contact_opening.v1"
    assert contract["required_sequence"] == [
        "what_kura_is",
        "how_to_use",
        "onboarding_interview_offer",
    ]
    assert contract["interview_offer"]["required"] is True
    assert contract["interview_offer"]["max_estimated_minutes"] == 5
    assert (
        "Kura is a structured training-data system."
        in contract["mandatory_sentence"]
    )


def test_bootstrap_onboarding_agenda_requires_intro_then_interview_offer() -> None:
    agent_route = AGENT_ROUTE.read_text(encoding="utf-8")
    assert (
        "First contact. Briefly explain Kura and how to use it, then offer a short onboarding interview to bootstrap profile."
        in agent_route
    )
