from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "supabase-cutover.md"


def test_deploy_recreates_proxy_after_api_worker_rollout() -> None:
    src = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "up -d kura-postgres kura-api kura-worker" in src
    assert "up -d --force-recreate kura-proxy" in src
    assert "refresh upstream binding" in src


def test_runbook_requires_force_recreate_proxy_for_cutover_and_rollback() -> None:
    src = RUNBOOK.read_text(encoding="utf-8")

    assert "up -d kura-api kura-worker" in src
    assert "up -d --force-recreate kura-proxy" in src
    assert "Rollback Trigger Matrix" in src
