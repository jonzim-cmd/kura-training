from __future__ import annotations

from pathlib import Path


AGENT_RS = Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent.rs"


def test_memory_tier_contract_markers_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "AGENT_MEMORY_TIER_CONTRACT_VERSION" in src
    assert "struct AgentMemoryTierContract" in src
    assert "struct AgentMemoryTierSnapshot" in src
    assert "build_memory_tier_contract" in src


def test_memory_tier_gate_reason_codes_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "memory_principles_stale_confirm_first" in src
    assert "memory_principles_missing_confirm_first" in src
