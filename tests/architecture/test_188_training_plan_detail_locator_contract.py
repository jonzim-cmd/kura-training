from __future__ import annotations

import sys
from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))

TRAINING_PLAN_HANDLER = Path("workers/src/kura_workers/handlers/training_plan.py")
AGENT_ROUTE = Path("api/src/routes/agent.rs")


def test_system_config_declares_training_plan_detail_retrieval_contract() -> None:
    from kura_workers.system_config import _get_conventions

    contract = _get_conventions()["training_plan_detail_retrieval_v1"]
    assert contract["schema_version"] == "training_plan_detail_retrieval.v1"
    assert contract["primary_projection"]["projection_type"] == "training_plan"
    assert contract["primary_projection"]["key"] == "overview"
    assert contract["detail_projection"]["projection_type"] == "training_plan"
    assert contract["detail_projection"]["key"] == "details"
    assert contract["fallback_event_policy"]["event_type"] == "training_plan.updated"


def test_training_plan_handler_persists_detail_locator_and_details_projection() -> None:
    src = TRAINING_PLAN_HANDLER.read_text(encoding="utf-8")
    assert "TRAINING_PLAN_DETAILS_KEY = \"details\"" in src
    assert "\"detail_locator\"" in src
    assert "\"projection_key\": TRAINING_PLAN_DETAILS_KEY" in src
    assert "\"plan_payload\": active_payload if active_plan else None" in src


def test_agent_reload_hint_mentions_training_plan_details_projection() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "/v1/projections/training_plan/overview" in src
    assert "/v1/projections/training_plan/details" in src


def test_agent_runtime_reload_hint_contract_for_training_plan_details() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::agent_context_reload_hint_for_training_plan_mentions_details_projection"
    )
