from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


def _event(event_type: str, data: dict[str, object], iso_ts: str) -> dict[str, object]:
    return {
        "event_type": event_type,
        "data": data,
        "timestamp": datetime.fromisoformat(iso_ts).astimezone(timezone.utc),
    }


def test_inv004_grandfathers_planning_before_policy_cutoff() -> None:
    from kura_workers.handlers.quality_health import _evaluate_read_only_invariants

    rows = [
        _event("training_plan.created", {"name": "legacy plan"}, "2026-02-11T13:00:00+00:00"),
        _event(
            "preference.set",
            {"key": "timezone", "value": "Europe/Berlin"},
            "2026-02-11T13:05:00+00:00",
        ),
        _event(
            "profile.updated",
            {"age_deferred": True, "bodyweight_deferred": True},
            "2026-02-11T13:10:00+00:00",
        ),
    ]
    issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})
    assert all(issue["invariant_id"] != "INV-004" for issue in issues)
    assert metrics["planning_event_total"] == 1
    assert metrics["planning_event_enforced_total"] == 0
    assert metrics["planning_event_legacy_total"] == 1
    assert metrics["inv004_legacy_policy_applied"] is True


def test_inv004_enforces_post_cutoff_planning_without_close() -> None:
    from kura_workers.handlers.quality_health import _evaluate_read_only_invariants

    rows = [
        _event("training_plan.created", {"name": "new plan"}, "2026-02-20T13:00:00+00:00"),
        _event(
            "preference.set",
            {"key": "timezone", "value": "Europe/Berlin"},
            "2026-02-20T13:05:00+00:00",
        ),
        _event(
            "profile.updated",
            {"age_deferred": True, "bodyweight_deferred": True},
            "2026-02-20T13:10:00+00:00",
        ),
    ]
    issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})
    inv004 = next((issue for issue in issues if issue["invariant_id"] == "INV-004"), None)
    assert inv004 is not None
    assert inv004["metrics"]["planning_event_count"] == 1
    assert inv004["metrics"]["legacy_grandfathered_count"] == 0
    assert metrics["planning_event_enforced_total"] == 1
