from __future__ import annotations

from pathlib import Path


ROUTES_DIR = Path(__file__).resolve().parents[2] / "api" / "src" / "routes"
AGENT_SOURCE_FILES = [ROUTES_DIR / "agent.rs", *sorted((ROUTES_DIR / "agent").glob("*.rs"))]


def _combined_agent_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in AGENT_SOURCE_FILES)


def test_user_language_guard_contract_markers_exist() -> None:
    src = _combined_agent_source()
    assert "enum AgentLanguageMode" in src
    assert "UserSafe" in src
    assert "DeveloperRaw" in src
    assert "KURA_AGENT_DEVELOPER_RAW_USER_ALLOWLIST_ENV" in src
    assert "AGENT_LANGUAGE_MODE_HEADER" in src
    assert "resolve_agent_language_mode" in src
    assert "apply_user_safe_language_guard" in src


def test_user_language_guard_pipeline_is_fail_open_with_single_rewrite_pass() -> None:
    src = _combined_agent_source()
    assert "rewrite_user_facing_fields_once" in src
    assert "if leak_count_before == 0" in src
    assert "leak_passed_through_total" in src
    assert "fail-open, one rewrite" in src
