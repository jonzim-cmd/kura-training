"""Unit tests for consistency inbox aggregation."""

from datetime import datetime, timedelta, timezone

from kura_workers.consistency_inbox import build_consistency_inbox


def test_consistency_inbox_includes_quality_health_issue_and_marks_action_required():
    now = datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc)
    result = build_consistency_inbox(
        quality_events=[],
        user_id="user-1",
        decisions=[],
        quality_health_issues=[
            {
                "issue_id": "INV-008:tempo_missing",
                "type": "set_context_missing_mention_bound_field",
                "severity": "medium",
                "detail": "Tempo mentioned in notes but not persisted in structured field.",
                "status": "open",
                "proposal_state": "simulated_risky",
                "detected_at": "2026-02-21T11:00:00Z",
            }
        ],
        now=now,
    )

    assert result["pending_items_total"] == 1
    assert result["highest_severity"] == "warning"
    assert result["requires_human_decision"] is True
    item = result["items"][0]
    assert item["source_type"] == "quality_health_issue"
    assert item["issue_id"] == "INV-008:tempo_missing"
    assert item["recommended_action"].startswith("Ask for explicit user decision")


def test_consistency_inbox_respects_snooze_cooldown_for_quality_health_issue():
    now = datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc)
    issue = {
        "issue_id": "INV-008:tempo_missing",
        "type": "set_context_missing_mention_bound_field",
        "severity": "medium",
        "detail": "Tempo mentioned in notes but not persisted in structured field.",
        "status": "open",
        "detected_at": "2026-02-21T11:00:00Z",
    }
    initial = build_consistency_inbox(
        quality_events=[],
        user_id="user-1",
        decisions=[],
        quality_health_issues=[issue],
        now=now,
    )
    item_id = initial["items"][0]["item_id"]
    decisions = [
        {
            "timestamp": now - timedelta(hours=1),
            "data": {
                "decision": "snooze",
                "item_ids": [item_id],
                "snooze_until": (now + timedelta(hours=5)).isoformat(),
            },
        }
    ]

    result = build_consistency_inbox(
        quality_events=[],
        user_id="user-1",
        decisions=decisions,
        quality_health_issues=[issue],
        now=now,
    )

    assert result["pending_items_total"] == 0
    assert result["requires_human_decision"] is False
    assert result["prompt_control"]["cooldown_active"] is True


def test_consistency_inbox_escalates_low_severity_quality_issue_when_open():
    now = datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc)
    result = build_consistency_inbox(
        quality_events=[],
        user_id="user-2",
        decisions=[],
        quality_health_issues=[
            {
                "issue_id": "INV-010:minor_signal",
                "type": "minor_quality_signal",
                "severity": "low",
                "detail": "Low-severity quality issue remains open.",
                "status": "open",
                "detected_at": "2026-02-21T11:30:00Z",
            }
        ],
        now=now,
    )

    assert result["pending_items_total"] == 1
    assert result["highest_severity"] == "info"
    assert result["requires_human_decision"] is True
