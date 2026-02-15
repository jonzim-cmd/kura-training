from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"
DRIFT_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check-migration-drift.sh"
COMPOSE_PROD = REPO_ROOT / "docker" / "compose.production.yml"


def test_deploy_flow_runs_migration_drift_preflight() -> None:
    src = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "check-migration-drift.sh" in src
    assert "--database-url \"$TARGET_DATABASE_URL\"" in src
    assert "--migrations-dir \"${ROOT_DIR}/migrations\"" in src


def test_migration_drift_script_validates_sqlx_migration_table() -> None:
    src = DRIFT_CHECK_SCRIPT.read_text(encoding="utf-8")

    assert "FROM _sqlx_migrations" in src
    assert "WHERE success = TRUE" in src
    assert "WHERE success = FALSE" in src
    assert "comm -23" in src  # required but missing in DB
    assert "comm -13" in src  # applied in DB but missing in repo
    assert "Schema drift detected" in src


def test_production_compose_requires_explicit_supabase_urls() -> None:
    src = COMPOSE_PROD.read_text(encoding="utf-8")

    assert "KURA_API_DATABASE_URL:?KURA_API_DATABASE_URL must be set" in src
    assert "KURA_WORKER_DATABASE_URL:?KURA_WORKER_DATABASE_URL must be set" in src
    assert "DATABASE_URL: ${KURA_API_DATABASE_URL:-postgresql://kura:" not in src
    assert "DATABASE_URL: ${KURA_WORKER_DATABASE_URL:-postgresql://kura:" not in src
