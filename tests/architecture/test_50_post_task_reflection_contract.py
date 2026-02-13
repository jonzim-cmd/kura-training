from __future__ import annotations

from pathlib import Path

from kura_workers.learning_telemetry import core_signal_types, signal_category


AGENT_RS = Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent.rs"


def test_post_task_reflection_contract_markers_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "POST_TASK_REFLECTION_SCHEMA_VERSION" in src
    assert "struct AgentPostTaskReflection" in src
    assert "build_post_task_reflection" in src
    assert "post_task_reflection" in src


def test_reflection_signal_taxonomy_is_registered() -> None:
    assert "post_task_reflection_confirmed" in core_signal_types()
    assert "post_task_reflection_partial" in core_signal_types()
    assert "post_task_reflection_unresolved" in core_signal_types()
    assert signal_category("post_task_reflection_confirmed") == "outcome_signal"
    assert signal_category("post_task_reflection_partial") == "friction_signal"
    assert signal_category("post_task_reflection_unresolved") == "friction_signal"
