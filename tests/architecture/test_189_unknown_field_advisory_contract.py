from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from kura_workers.handlers.quality_health import _evaluate_read_only_invariants
from kura_workers.handlers.user_profile import _escalate_priority
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes

USER_PROFILE = Path("workers/src/kura_workers/handlers/user_profile.py")

RUNTIME_TESTS: tuple[str, ...] = (
    "routes::events::tests::test_unknown_field_advisory_warns_for_registered_event_type",
    "routes::events::tests::test_unknown_field_advisory_skips_known_fields",
    "routes::events::tests::test_parse_unknown_field_mapping_hints_from_contract_shape",
)


def _row(event_type: str, data: dict) -> dict:
    return {"event_type": event_type, "data": data}


def test_events_runtime_unknown_field_advisory_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)


def test_quality_health_inv011_is_advisory_signal() -> None:
    rows = [
        _row("energy.logged", {"energy_level": 6}),
        _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
        _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
    ]
    issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

    issue = next((item for item in issues if item["invariant_id"] == "INV-011"), None)
    assert issue is not None
    assert issue["severity"] in {"low", "medium"}
    assert issue["severity"] != "high"
    assert issue["metrics"]["unknown_field_occurrences_recent"] >= 1
    assert metrics["unknown_field_occurrences_recent"] >= 1


def test_unknown_field_advisory_contract_is_warn_only_and_contains_mapping_hints() -> None:
    contract = _get_conventions()["unknown_field_advisory_v1"]["contract"]
    assert contract["schema_version"] == "unknown_field_advisory.v1"
    assert contract["policy_role"] == "advisory_only"
    assert contract["write_policy"]["mode"] == "warn_only"
    assert contract["write_policy"]["block_unknown_fields"] is False
    hints = {
        (item["event_type"], item["field"]): item["mapped_field"]
        for item in contract["mapping_hints"]
    }
    assert hints[("energy.logged", "energy_level")] == "level"
    assert hints[("session.completed", "overall_feeling")] == "enjoyment"


def test_user_profile_seen_query_batches_event_types_and_fields() -> None:
    src = USER_PROFILE.read_text(encoding="utf-8")
    assert "AND e.event_type = ANY(%s)" in src
    assert "AND k = ANY(%s)" in src
    assert "GROUP BY e.event_type, k" in src


def test_user_profile_observed_field_priority_decays_when_stale() -> None:
    stale_last_seen = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    assert _escalate_priority(150, None) == "high"
    assert _escalate_priority(150, stale_last_seen) == "low"
