from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from kura_workers.handlers.quality_health import _compute_draft_hygiene_metrics
from kura_workers.system_config import _get_agent_behavior
from tests.architecture.conftest import assert_kura_api_test_passes

AGENT_ROUTE = Path("api/src/routes/agent.rs")
OBSERVATION_CLI = Path("cli/src/commands/observation.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::observation_draft_context_contract_schema_version_is_pinned",
    "routes::agent::tests::observation_draft_context_contract_maps_recent_drafts_and_age",
    "routes::agent::tests::observation_draft_context_contract_maps_list_item_contract",
    "routes::agent::tests::observation_draft_promotion_contract_requires_non_plan_formal_event_type",
    "routes::agent::tests::observation_draft_resolution_contract_requires_non_provisional_dimension",
    "routes::agent::tests::observation_draft_resolution_contract_sanitizes_persist_intent_tags",
    "routes::agent::tests::observation_draft_dismiss_reason_defaults_for_blank_input",
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
        "review_status",
        "review_loop_required",
        "next_action_hint",
        "recent_drafts[]",
    ]
    assert contract["recent_drafts_item_fields"] == [
        "observation_id",
        "timestamp",
        "summary",
    ]
    assert contract["review_status_levels"] == ["healthy", "monitor", "degraded"]


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
    minimal = endpoints["promote_minimal_payload"]
    assert minimal["event_type"] == "set.logged"
    assert "data" in minimal
    guards = contract["promote_write_guards"]
    assert guards["requires_formal_event_type"] is True
    assert guards["reject_retraction_as_formal_target"] is True
    assert guards["enforce_legacy_domain_invariants"] is True
    assert guards["atomic_formal_write_plus_retract"] is True


def test_observation_draft_resolution_contract_is_declared() -> None:
    operational = _operational()
    contract = operational["observation_draft_resolution_v1"]
    assert contract["schema_version"] == "observation_draft_resolve.v1"
    api_contract = contract["api_contract"]
    assert (
        api_contract["resolve_endpoint"]
        == "POST /v1/agent/observation-drafts/{observation_id}/resolve-as-observation"
    )
    assert api_contract["resolve_minimal_payload"] == {"dimension": "competition_note"}
    guards = contract["resolve_write_guards"]
    assert guards["requires_non_provisional_dimension"] is True
    assert guards["event_type"] == "observation.logged"
    assert guards["atomic_observation_write_plus_retract"] is True
    assert guards["default_retract_reason"] == "resolved_as_observation"


def test_observation_draft_dismissal_contract_is_declared() -> None:
    operational = _operational()
    contract = operational["observation_draft_dismissal_v1"]
    assert contract["schema_version"] == "observation_draft_dismiss.v1"
    api_contract = contract["api_contract"]
    assert (
        api_contract["dismiss_endpoint"]
        == "POST /v1/agent/observation-drafts/{observation_id}/dismiss"
    )
    assert api_contract["dismiss_payload_optional"] is True
    assert api_contract["dismiss_reason_example"] == {"reason": "duplicate"}
    guards = contract["dismiss_write_guards"]
    assert guards["event_type"] == "event.retracted"
    assert guards["target_event_type"] == "observation.logged"
    assert guards["default_reason"] == "dismissed_non_actionable"


def test_observation_draft_review_loop_contract_is_declared() -> None:
    operational = _operational()
    contract = operational["observation_draft_review_loop_v1"]
    assert contract["schema_version"] == "observation_draft_review_loop.v1"
    assert "observations_draft.open_count > 0" in contract["trigger_when"]
    endpoints = contract["close_endpoints"]
    assert endpoints["list"] == "GET /v1/agent/observation-drafts"
    assert (
        endpoints["dismiss"]
        == "POST /v1/agent/observation-drafts/{observation_id}/dismiss"
    )
    assert (
        endpoints["resolve"]
        == "POST /v1/agent/observation-drafts/{observation_id}/resolve-as-observation"
    )
    assert (
        endpoints["promote"]
        == "POST /v1/agent/observation-drafts/{observation_id}/promote"
    )
    targets = contract["hygiene_targets"]
    assert targets["window_days"] == 7
    assert targets["backlog_monitor_min"] == 2
    assert targets["backlog_degraded_min"] == 5
    assert any("duplicate/test/noise => dismiss" in step for step in contract["review_steps"])


def test_observation_draft_list_query_guarantees_oldest_first_and_recent_context_path() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "ORDER BY e.timestamp ASC, e.id ASC" in src
    assert "async fn fetch_recent_draft_observations" in src
    assert "ORDER BY e.timestamp DESC, e.id DESC" in src


def test_observation_draft_dismiss_is_exposed_in_cli_and_mcp_surfaces() -> None:
    cli_src = OBSERVATION_CLI.read_text(encoding="utf-8")
    mcp_src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "Dismiss(ObservationDraftDismissArgs)" in cli_src
    assert "/v1/agent/observation-drafts/{}/dismiss" in cli_src
    assert "kura_observation_draft_dismiss" in mcp_src


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
