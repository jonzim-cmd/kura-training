from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


def _issue() -> dict[str, object]:
    return {
        "issue_id": "INV-004:onboarding_phase_violation",
        "invariant_id": "INV-004",
        "type": "onboarding_phase_violation",
        "severity": "medium",
        "detail": "planning drift",
        "metrics": {},
    }


def test_quality_issue_detected_dedupes_within_cooldown() -> None:
    from kura_workers.handlers.quality_health import _build_detection_learning_signal_events

    now = datetime.now(tz=timezone.utc)
    history = {
        "INV-004:onboarding_phase_violation": {
            "timestamp": now - timedelta(hours=3),
            "severity": "medium",
        }
    }
    events = _build_detection_learning_signal_events(
        user_id="user-1",
        issues=[_issue()],
        proposals=[],
        evaluated_at=now.isoformat(),
        source_anchor="anchor-1",
        quality_issue_history_by_issue=history,
    )
    signal_types = [event["data"]["signal_type"] for event in events]
    assert "quality_issue_detected" not in signal_types


def test_quality_issue_detected_re_emits_after_cooldown() -> None:
    from kura_workers.handlers.quality_health import _build_detection_learning_signal_events

    now = datetime.now(tz=timezone.utc)
    history = {
        "INV-004:onboarding_phase_violation": {
            "timestamp": now - timedelta(hours=30),
            "severity": "medium",
        }
    }
    events = _build_detection_learning_signal_events(
        user_id="user-1",
        issues=[_issue()],
        proposals=[],
        evaluated_at=now.isoformat(),
        source_anchor="anchor-1",
        quality_issue_history_by_issue=history,
    )
    detected = [
        event for event in events if event["data"]["signal_type"] == "quality_issue_detected"
    ]
    assert len(detected) == 1
    attrs = detected[0]["data"]["attributes"]
    assert attrs["cooldown_active"] is False
    assert attrs["dedupe_cooldown_hours"] == 24
