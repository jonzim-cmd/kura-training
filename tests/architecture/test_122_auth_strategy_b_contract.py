from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "supabase-cutover.md"
AUTH_ROUTE = REPO_ROOT / "api" / "src" / "routes" / "auth.rs"


def test_decision_record_pins_auth_strategy_b_for_launch_track() -> None:
    src = CLAUDE_MD.read_text(encoding="utf-8")

    assert "AUTH_STRATEGY=B" in src
    assert "Full Supabase Auth ist explizit post-launch Scope" in src
    assert "`users`, `api_keys`, `oauth_*` bleiben die Auth-Source-of-Truth" in src


def test_runbook_scopes_cutover_to_db_only_auth_compatibility() -> None:
    src = RUNBOOK.read_text(encoding="utf-8")

    assert "DB-only" in src
    assert "Auth strategy is fixed to `AUTH_STRATEGY=B`" in src
    assert "without Supabase Auth token issuance" in src


def test_auth_runtime_stays_on_internal_oauth_tables_not_supabase_auth_endpoints() -> None:
    src = AUTH_ROUTE.read_text(encoding="utf-8")

    assert "FROM oauth_authorization_codes" in src
    assert "FROM oauth_refresh_tokens" in src
    assert "INSERT INTO oauth_access_tokens" in src
    assert "INSERT INTO oauth_refresh_tokens" in src
    assert "supabase.co/auth/v1" not in src
