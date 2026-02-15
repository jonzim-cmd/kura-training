from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
INIT_SQL = REPO_ROOT / "docker" / "postgres" / "init.sql"
COMPOSE_PROD = REPO_ROOT / "docker" / "compose.production.yml"
WORKER_CONFIG = REPO_ROOT / "workers" / "src" / "kura_workers" / "config.py"
WORKER_IMPL = REPO_ROOT / "workers" / "src" / "kura_workers" / "worker.py"
QUALITY_HEALTH = REPO_ROOT / "workers" / "src" / "kura_workers" / "handlers" / "quality_health.py"
API_ROUTES_DIR = REPO_ROOT / "api" / "src" / "routes"


def test_supabase_decision_record_declares_hard_gate_keys() -> None:
    src = CLAUDE_MD.read_text(encoding="utf-8")

    assert "AUTH_STRATEGY=B" in src
    assert "app_writer" in src
    assert "app_reader" in src
    assert "app_worker" in src
    assert "app_migrator" in src
    assert "KURA_WORKER_LISTEN_DATABASE_URL" in src
    assert "`users`, `api_keys`, `oauth_*`" in src


def test_supabase_role_model_is_kept_as_custom_app_roles() -> None:
    src = INIT_SQL.read_text(encoding="utf-8")

    assert "CREATE ROLE app_writer;" in src
    assert "CREATE ROLE app_reader;" in src
    assert "CREATE ROLE app_migrator;" in src
    assert "CREATE ROLE app_worker BYPASSRLS;" in src
    assert "GRANT app_writer TO kura;" in src
    assert "GRANT app_reader TO kura;" in src
    assert "GRANT app_migrator TO kura;" in src
    assert "GRANT app_worker TO kura;" in src


def test_worker_connection_policy_supports_dedicated_listen_url() -> None:
    cfg_src = WORKER_CONFIG.read_text(encoding="utf-8")
    worker_src = WORKER_IMPL.read_text(encoding="utf-8")
    compose_src = COMPOSE_PROD.read_text(encoding="utf-8")

    assert "listen_database_url: str" in cfg_src
    assert 'os.environ.get("KURA_WORKER_LISTEN_DATABASE_URL", "")' in cfg_src
    assert "listen_database_url = database_url" in cfg_src
    assert "self.config.listen_database_url, autocommit=True" in worker_src
    assert "KURA_WORKER_LISTEN_DATABASE_URL" in compose_src


def test_api_rls_context_is_bound_to_transaction_handle() -> None:
    offenders: list[str] = []
    for path in sorted(API_ROUTES_DIR.rglob("*.rs")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if "set_config('kura.current_user_id'" not in line:
                continue
            window = "\n".join(lines[idx : idx + 8])
            if "execute(&mut" not in window:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{idx + 1}")

    assert not offenders, (
        "kura.current_user_id must be set via transaction-bound executor. "
        f"Offenders: {', '.join(offenders)}"
    )


def test_worker_role_local_overrides_remain_transactional_for_repair_path() -> None:
    worker_src = WORKER_IMPL.read_text(encoding="utf-8")
    quality_src = QUALITY_HEALTH.read_text(encoding="utf-8")

    assert "async with conn.transaction():" in worker_src
    assert 'await conn.execute("SET LOCAL ROLE app_writer")' in quality_src
    assert 'await conn.execute("SET LOCAL ROLE app_worker")' in quality_src
