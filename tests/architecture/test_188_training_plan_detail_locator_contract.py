from __future__ import annotations

import sys
from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))

def test_system_config_declares_training_plan_detail_retrieval_contract() -> None:
    from kura_workers.system_config import _get_conventions

    contract = _get_conventions()["training_plan_detail_retrieval_v1"]
    assert contract["schema_version"] == "training_plan_detail_retrieval.v1"
    assert contract["primary_projection"]["projection_type"] == "training_plan"
    assert contract["primary_projection"]["key"] == "overview"
    assert contract["detail_projection"]["projection_type"] == "training_plan"
    assert contract["detail_projection"]["key"] == "details"
    assert contract["fallback_event_policy"]["event_type"] == "training_plan.updated"
    assert all("source_event_ref" not in rule for rule in contract["rules"])
    assert any("source_event" in rule for rule in contract["rules"])


def test_training_plan_handler_builds_detail_locator_and_details_projection() -> None:
    from kura_workers.handlers import training_plan as handler

    plans = {
        "default": {
            "plan_id": "default",
            "name": "Jump Focus",
            "created_at": "2026-02-20T10:00:00+00:00",
            "updated_at": "2026-02-21T10:00:00+00:00",
            "status": "active",
            "sessions": [],
            "cycle_weeks": 4,
            "notes": "In-season",
            "rir_targets": {
                "exercises_total": 1,
                "exercises_with_target_rir": 1,
                "inferred_target_rir": 0,
                "average_target_rir": 2.0,
            },
        }
    }
    plan_payloads = {
        "default": {
            "name": "Jump Focus",
            "sessions": [
                {
                    "name": "Monday",
                    "exercises": [
                        {
                            "exercise_id": "depth_jump",
                            "name": "Depth Jumps",
                            "sets": 4,
                            "reps": 5,
                        }
                    ],
                }
            ],
            "notes": "In-season",
        }
    }
    plan_detail_sources = {
        "default": {
            "event_id": "evt-123",
            "event_type": "training_plan.updated",
            "timestamp": "2026-02-21T10:00:00+00:00",
        }
    }
    overview, details, plan_name = handler._build_training_plan_projection_payloads(
        plans,
        plan_payloads,
        plan_detail_sources,
        [],
    )

    assert handler.TRAINING_PLAN_DETAILS_KEY == "details"
    assert plan_name == "Jump Focus"
    assert overview["detail_locator"]["projection_key"] == handler.TRAINING_PLAN_DETAILS_KEY
    assert overview["detail_locator"]["detail_level"] == "structured"
    assert overview["detail_locator"]["source_event"] == plan_detail_sources["default"]
    assert details["schema_version"] == handler.TRAINING_PLAN_DETAILS_SCHEMA_VERSION
    assert details["detail_level"] == "structured"
    assert details["source_event"] == plan_detail_sources["default"]
    assert details["plan_payload"] == plan_payloads["default"]


def test_agent_runtime_reload_hint_contract_for_training_plan_details() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::agent_context_reload_hint_for_training_plan_mentions_details_projection"
    )
