from __future__ import annotations

from pathlib import Path


AGENT_RS = Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent.rs"


def test_intent_handshake_contract_markers_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "INTENT_HANDSHAKE_SCHEMA_VERSION" in src
    assert "struct AgentIntentHandshake" in src
    assert "validate_intent_handshake" in src
    assert "intent_handshake is required for high-impact writes" in src


def test_intent_handshake_confirmation_is_part_of_write_response() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "struct AgentIntentHandshakeConfirmation" in src
    assert "intent_handshake_confirmation" in src
