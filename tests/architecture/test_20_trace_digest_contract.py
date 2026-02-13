from __future__ import annotations

from pathlib import Path


AGENT_RS = Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent.rs"


def test_trace_digest_contract_markers_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "TRACE_DIGEST_SCHEMA_VERSION" in src
    assert "struct AgentTraceDigest" in src
    assert "build_trace_digest" in src
    assert "trace_digest" in src


def test_trace_digest_contains_chat_summary_contract() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "chat_summary_template_id" in src
    assert "trace_digest.chat.short.v1" in src
