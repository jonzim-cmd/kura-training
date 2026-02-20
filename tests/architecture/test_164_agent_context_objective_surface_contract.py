from __future__ import annotations

from pathlib import Path

AGENT_ROUTE = Path("api/src/routes/agent.rs")
AGENT_POLICY = Path("api/src/routes/agent/policy.rs")


def test_agent_context_surface_contains_objective_state_and_advisory() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub objective_state: Option<ProjectionResponse>" in src
    assert "pub objective_advisory: Option<ProjectionResponse>" in src
    assert "fetch_projection(&mut tx, user_id, \"objective_state\", \"active\")" in src
    assert "fetch_projection(&mut tx, user_id, \"objective_advisory\", \"overview\")" in src


def test_agent_context_overflow_and_reload_hints_include_objective_surfaces() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "\"objective_state\" => \"Reload with GET /v1/projections/objective_state/active.\"" in src
    assert (
        "\"objective_advisory\" => \"Reload with GET /v1/projections/objective_advisory/overview.\""
        in src
    )
    assert "\"objective_advisory\"," in src
    assert "\"objective_state\"," in src


def test_agent_context_contract_version_pins_objective_surfaces_extension() -> None:
    src = AGENT_POLICY.read_text(encoding="utf-8")
    assert "AGENT_CONTEXT_CONTRACT_VERSION" in src
    assert "objective_surfaces" in src
