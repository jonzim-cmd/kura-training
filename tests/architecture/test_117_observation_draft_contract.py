from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kura_workers.handlers.quality_health import _compute_draft_hygiene_metrics
from kura_workers.system_config import _get_agent_behavior
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::observation_draft_context_contract_schema_version_is_pinned",
    "routes::agent::tests::observation_draft_context_contract_maps_recent_drafts_and_age",
    "routes::agent::tests::observation_draft_context_contract_maps_list_item_contract",
    "routes::agent::tests::observation_draft_promotion_contract_requires_non_plan_formal_event_type",
)


def _operational() -> dict:
    return _get_agent_behavior()["operational"]


def _draft_observation_row(event_id: str, timestamp: datetime) -> dict:
    return {
        "id": event_id,
        "event_type": "observation.logged",
        "timestamp": timestamp,
        "data": {"dimension": "provisional.persist_intent.training_plan"},
    }


def test_observation_draft_context_contract_is_declared() -> None:
    operational = _operational()
    contract = operational["observation_draft_context_v1"]
    assert contract["schema_version"] == "observation_draft_context.v1"
    assert contract["source_contract"]["event_type"] == "observation.logged"
    assert contract["source_contract"]["dimension_prefix"] == "provisional.persist_intent."
    assert contract["source_contract"]["projection_type"] == "open_observations"
    assert contract["context_fields"] == [
        "open_count",
        "oldest_draft_age_hours",
        "recent_drafts[]",
    ]
    assert contract["recent_drafts_item_fields"] == [
        "observation_id",
        "timestamp",
        "summary",
    ]


def test_observation_draft_promotion_contract_is_declared() -> None:
    operational = _operational()
    contract = operational["observation_draft_promotion_v1"]
    assert contract["schema_version"] == "observation_draft_promote.v1"
    endpoints = contract["api_contract"]
    assert endpoints["list_endpoint"] == "GET /v1/agent/observation-drafts"
    assert (
        endpoints["detail_endpoint"]
        == "GET /v1/agent/observation-drafts/{observation_id}"
    )
    assert (
        endpoints["promote_endpoint"]
        == "POST /v1/agent/observation-drafts/{observation_id}/promote"
    )
    guards = contract["promote_write_guards"]
    assert guards["requires_formal_event_type"] is True
    assert guards["reject_retraction_as_formal_target"] is True
    assert guards["enforce_legacy_domain_invariants"] is True
    assert guards["atomic_formal_write_plus_retract"] is True


def test_draft_hygiene_contract_is_declared_and_thresholds_behave() -> None:
    operational = _operational()
    contract = operational["draft_hygiene_feedback_v1"]
    assert contract["schema_version"] == "draft_hygiene_feedback.v1"
    assert contract["status_levels"] == ["healthy", "monitor", "degraded"]
    assert contract["window_days"] == 7

    evaluated_at = datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc)
    healthy_rows = [_draft_observation_row("draft-1", evaluated_at - timedelta(hours=2))]
    monitor_rows = [
        _draft_observation_row("draft-1", evaluated_at - timedelta(hours=2)),
        _draft_observation_row("draft-2", evaluated_at - timedelta(hours=9)),
    ]
    degraded_rows = [
        _draft_observation_row("draft-1", evaluated_at - timedelta(hours=1)),
        _draft_observation_row("draft-2", evaluated_at - timedelta(hours=2)),
        _draft_observation_row("draft-3", evaluated_at - timedelta(hours=3)),
        _draft_observation_row("draft-4", evaluated_at - timedelta(hours=4)),
        _draft_observation_row("draft-5", evaluated_at - timedelta(hours=5)),
    ]

    healthy = _compute_draft_hygiene_metrics(healthy_rows, healthy_rows, evaluated_at)
    monitor = _compute_draft_hygiene_metrics(monitor_rows, monitor_rows, evaluated_at)
    degraded = _compute_draft_hygiene_metrics(degraded_rows, degraded_rows, evaluated_at)

    assert healthy["status"] == "healthy"
    assert monitor["status"] == "monitor"
    assert degraded["status"] == "degraded"


def test_observation_draft_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
