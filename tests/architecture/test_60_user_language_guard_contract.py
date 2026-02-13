from __future__ import annotations

from pathlib import Path


AGENT_RS = Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent.rs"


def test_user_language_guard_contract_markers_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "enum AgentLanguageMode" in src
    assert "UserSafe" in src
    assert "DeveloperRaw" in src
    assert "KURA_AGENT_DEVELOPER_RAW_USER_ALLOWLIST_ENV" in src
    assert "AGENT_LANGUAGE_MODE_HEADER" in src
    assert "resolve_agent_language_mode" in src
    assert "apply_user_safe_language_guard" in src


def test_user_language_guard_pipeline_is_fail_open_with_single_rewrite_pass() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "rewrite_user_facing_fields_once" in src
    assert "if leak_count_before == 0" in src
    assert "leak_passed_through_total" in src
    assert "fail-open, one rewrite" in src
