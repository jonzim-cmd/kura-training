from __future__ import annotations

from pathlib import Path

OBJECTIVE_ADVISORY_HANDLER = Path("workers/src/kura_workers/handlers/objective_advisory.py")
AGENT_ROUTE = Path("api/src/routes/agent.rs")


def test_objective_advisory_projection_exposes_override_rationale_summary() -> None:
    src = OBJECTIVE_ADVISORY_HANDLER.read_text(encoding="utf-8")
    assert "\"override_summary\"" in src
    assert "\"review_due_count\"" in src
    assert "\"by_actor\"" in src
    assert "\"by_scope\"" in src
    assert "\"recent\"" in src


def test_agent_context_contract_can_surface_override_rationale_via_objective_advisory() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub objective_advisory: Option<ProjectionResponse>" in src
    assert "projections.objective_advisory" in src
