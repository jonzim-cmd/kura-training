from __future__ import annotations

from pathlib import Path

from kura_workers.event_conventions import get_event_conventions
from kura_workers.system_config import _get_conventions

HANDLERS_INIT = Path("workers/src/kura_workers/handlers/__init__.py")
AGENT_ROUTE = Path("api/src/routes/agent.rs")


def test_cross_layer_contract_declares_objective_and_advisory_surfaces() -> None:
    conventions = _get_conventions()
    assert "objective_contract_v1" in conventions
    assert "objective_advisory_v1" in conventions
    assert "objective_statistical_method_v1" in conventions
    assert "causal_estimand_registry_v2" in conventions


def test_event_surface_includes_objective_and_override_events() -> None:
    event_conventions = get_event_conventions()
    assert "goal.set" in event_conventions
    assert "objective.set" in event_conventions
    assert "objective.updated" in event_conventions
    assert "objective.archived" in event_conventions
    assert "advisory.override.recorded" in event_conventions


def test_projection_bootstrap_and_agent_context_expose_objective_layers() -> None:
    handlers_src = HANDLERS_INIT.read_text(encoding="utf-8")
    assert "from . import objective_state" in handlers_src
    assert "from . import objective_advisory" in handlers_src

    agent_src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "projections.objective_state" in agent_src
    assert "projections.objective_advisory" in agent_src
    assert "objective_state" in agent_src
    assert "objective_advisory" in agent_src
