from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


# ── Python-side contract assertions ──────────────────────────────────


def test_system_config_declares_consistency_inbox_protocol() -> None:
    """consistency_inbox_protocol_v1 must exist in agent_behavior.operational."""
    from kura_workers.system_config import _get_agent_behavior

    behavior = _get_agent_behavior()
    protocol = behavior["operational"]["consistency_inbox_protocol_v1"]
    assert protocol["approval_required_before_fix"] is True
    assert protocol["max_questions_per_turn"] == 1
    assert set(protocol["allowed_user_decisions"]) == {"approve", "decline", "snooze"}


def test_consistency_inbox_protocol_schema_version_is_pinned() -> None:
    from kura_workers.system_config import _get_agent_behavior

    behavior = _get_agent_behavior()
    protocol = behavior["operational"]["consistency_inbox_protocol_v1"]
    assert protocol["schema_version"] == "consistency_inbox_protocol.v1"


def test_consistency_inbox_protocol_has_decision_event() -> None:
    from kura_workers.system_config import _get_agent_behavior

    behavior = _get_agent_behavior()
    protocol = behavior["operational"]["consistency_inbox_protocol_v1"]
    decision = protocol["decision_event"]
    assert decision["event_type"] == "quality.consistency.review.decided"
    assert "item_ids" in decision["required_fields"]
    assert "decision" in decision["required_fields"]
    assert "decision_source" in decision["required_fields"]


def test_event_convention_declares_review_decided() -> None:
    from kura_workers.event_conventions import get_event_conventions

    conventions = get_event_conventions()
    assert "quality.consistency.review.decided" in conventions
    conv = conventions["quality.consistency.review.decided"]
    required = conv["fields"]["required"]
    assert "item_ids" in required
    assert "decision" in required
    assert "decision_source" in required


def test_consistency_inbox_projection_handler_is_registered() -> None:
    import kura_workers.handlers  # noqa: F401
    from kura_workers.registry import get_projection_handlers

    quality_handlers = {
        handler.__name__
        for handler in get_projection_handlers("quality.save_claim.checked")
    }
    decision_handlers = {
        handler.__name__
        for handler in get_projection_handlers("quality.consistency.review.decided")
    }

    assert "update_consistency_inbox" in quality_handlers
    assert "update_consistency_inbox" in decision_handlers


def test_build_consistency_inbox_produces_required_schema_fields() -> None:
    """build_consistency_inbox must return all fields from the projection schema."""
    from kura_workers.consistency_inbox import build_consistency_inbox

    now = datetime.now(tz=timezone.utc)
    result = build_consistency_inbox([], "test-user", now=now)

    assert "schema_version" in result
    assert "generated_at" in result
    assert "pending_items_total" in result
    assert "highest_severity" in result
    assert "requires_human_decision" in result
    assert "items" in result
    assert "prompt_control" in result
    assert result["pending_items_total"] == 0
    assert result["highest_severity"] == "none"
    assert result["requires_human_decision"] is False


def test_build_consistency_inbox_surfaces_critical_mismatch() -> None:
    from kura_workers.consistency_inbox import build_consistency_inbox

    now = datetime.now(tz=timezone.utc)
    events = [
        {
            "id": "evt-1",
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_detected": True,
                "mismatch_severity": "critical",
                "mismatch_weight": 1.0,
                "mismatch_reason_codes": ["save_echo_missing"],
            },
        },
    ]
    result = build_consistency_inbox(events, "test-user", now=now)

    assert result["pending_items_total"] == 1
    assert result["highest_severity"] == "critical"
    assert result["requires_human_decision"] is True
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["item_id"].startswith("ci-")
    assert item["severity"] == "critical"
    assert item["summary"]  # non-empty


def test_build_consistency_inbox_decline_cooldown() -> None:
    """After a decline, the same item must not reappear within cooldown period."""
    from kura_workers.consistency_inbox import (
        _stable_item_id,
        build_consistency_inbox,
    )

    now = datetime.now(tz=timezone.utc)
    item_id = _stable_item_id("test-user", "save_claim_mismatch", "save_echo_missing")

    events = [
        {
            "id": "evt-1",
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_severity": "critical",
                "mismatch_weight": 1.0,
                "mismatch_reason_codes": ["save_echo_missing"],
            },
        },
    ]
    decisions = [
        {
            "event_type": "quality.consistency.review.decided",
            "timestamp": now - timedelta(hours=2),
            "data": {
                "item_ids": [item_id],
                "decision": "decline",
                "decision_source": "chat_explicit",
            },
        },
    ]

    result = build_consistency_inbox(events, "test-user", decisions=decisions, now=now)
    # Declined item should be suppressed (within 7-day cooldown).
    assert result["pending_items_total"] == 0


def test_build_consistency_inbox_snooze_respects_until() -> None:
    """Snoozed items must not appear before snooze_until."""
    from kura_workers.consistency_inbox import (
        _stable_item_id,
        build_consistency_inbox,
    )

    now = datetime.now(tz=timezone.utc)
    item_id = _stable_item_id("test-user", "save_claim_mismatch", "save_echo_missing")
    snooze_until = (now + timedelta(hours=24)).isoformat()

    events = [
        {
            "id": "evt-1",
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_severity": "warning",
                "mismatch_weight": 0.5,
                "mismatch_reason_codes": ["save_echo_missing"],
            },
        },
    ]
    decisions = [
        {
            "event_type": "quality.consistency.review.decided",
            "timestamp": now - timedelta(hours=6),
            "data": {
                "item_ids": [item_id],
                "decision": "snooze",
                "decision_source": "chat_explicit",
                "snooze_until": snooze_until,
            },
        },
    ]

    result = build_consistency_inbox(events, "test-user", decisions=decisions, now=now)
    assert result["pending_items_total"] == 0
    assert result["prompt_control"]["cooldown_active"] is True


def test_build_consistency_inbox_approve_removes_cooldown() -> None:
    """After approve, the item cooldown is cleared."""
    from kura_workers.consistency_inbox import (
        _stable_item_id,
        build_consistency_inbox,
    )

    now = datetime.now(tz=timezone.utc)
    item_id = _stable_item_id("test-user", "save_claim_mismatch", "save_echo_missing")

    events = [
        {
            "id": "evt-1",
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_severity": "critical",
                "mismatch_weight": 1.0,
                "mismatch_reason_codes": ["save_echo_missing"],
            },
        },
    ]
    # First declined, then approved — approve clears cooldown.
    decisions = [
        {
            "event_type": "quality.consistency.review.decided",
            "timestamp": now - timedelta(hours=3),
            "data": {
                "item_ids": [item_id],
                "decision": "decline",
                "decision_source": "chat_explicit",
            },
        },
        {
            "event_type": "quality.consistency.review.decided",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "item_ids": [item_id],
                "decision": "approve",
                "decision_source": "chat_explicit",
            },
        },
    ]

    result = build_consistency_inbox(events, "test-user", decisions=decisions, now=now)
    # Approve clears cooldown, item reappears.
    assert result["pending_items_total"] == 1


def test_build_consistency_inbox_ignores_none_severity() -> None:
    """Events with mismatch_severity=none should not produce inbox items."""
    from kura_workers.consistency_inbox import build_consistency_inbox

    now = datetime.now(tz=timezone.utc)
    events = [
        {
            "id": "evt-1",
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_severity": "none",
                "mismatch_weight": 0.0,
            },
        },
    ]
    result = build_consistency_inbox(events, "test-user", now=now)
    assert result["pending_items_total"] == 0


# ── Rust runtime contract tests (delegated) ──────────────────────────


def test_consistency_inbox_contract_is_exposed_in_context() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::consistency_inbox_contract_is_exposed_in_context"
    )


def test_consistency_inbox_contract_requires_explicit_approval_before_fix() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::consistency_inbox_contract_requires_explicit_approval_before_fix"
    )


def test_consistency_inbox_contract_respects_snooze_cooldown() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::consistency_inbox_contract_respects_snooze_cooldown"
    )
