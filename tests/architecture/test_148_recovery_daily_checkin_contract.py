from __future__ import annotations

from datetime import date, datetime, timezone

from kura_workers.event_conventions import get_event_conventions
from kura_workers.handlers.user_profile import (
    _build_agenda,
    _build_session_rpe_follow_up_signal,
)
from kura_workers.recovery_daily_checkin import normalize_daily_checkin_payload


def test_daily_checkin_contract_declares_core_and_optional_fields() -> None:
    convention = get_event_conventions()["recovery.daily_checkin"]
    ux_contract = convention["ux_contract"]

    core = set(ux_contract["core_required_for_prompting"])
    optional = set(ux_contract["optional_context"])

    assert {"bodyweight_kg", "sleep_hours", "soreness", "motivation"} <= core
    assert {
        "hrv_rmssd",
        "sleep_quality",
        "physical_condition",
        "lifestyle_stability",
        "traveling_yesterday",
        "sick_today",
        "alcohol_last_night",
        "training_yesterday",
    } <= optional


def test_daily_checkin_parser_contract_supports_positional_and_space_key_value_fast_input() -> None:
    positional = normalize_daily_checkin_payload(
        {"compact_input": "78.4,7.2,3,8,62,no,no,0"}
    )
    assert positional["parsed_from_compact"] is True
    assert positional["compact_input_mode"] == "positional"
    assert positional["bodyweight_kg"] == 78.4
    assert positional["sleep_hours"] == 7.2
    assert positional["soreness"] == 3.0
    assert positional["motivation"] == 8.0
    assert positional["hrv_rmssd"] == 62.0
    assert positional["sick_today"] is False
    assert positional["traveling_yesterday"] is False
    assert positional["alcohol_last_night"] == "none"

    key_value = normalize_daily_checkin_payload(
        {"compact_input": "sleep=7.2 soreness=3 motivation=8 hrv=62"}
    )
    assert key_value["parsed_from_compact"] is True
    assert key_value["compact_input_mode"] == "key_value"
    assert key_value["sleep_hours"] == 7.2
    assert key_value["soreness"] == 3.0
    assert key_value["motivation"] == 8.0
    assert key_value["hrv_rmssd"] == 62.0


def test_user_profile_contract_surfaces_session_rpe_follow_up_for_recent_training() -> None:
    signal = _build_session_rpe_follow_up_signal(
        latest_timestamp=datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
        timezone_name="UTC",
        training_activity_by_day={date(2026, 2, 20): 1},
        session_feedback_by_day={},
        session_rpe_by_day={},
    )
    assert signal is not None
    assert signal["type"] == "missing_session_completed"
    assert signal["date"] == "2026-02-20"

    agenda = _build_agenda(
        unresolved_exercises=[],
        unconfirmed_aliases=[],
        session_rpe_follow_up=signal,
    )
    item = next(entry for entry in agenda if entry["type"] == "session_rpe_follow_up")
    assert item["priority"] == "medium"
    assert "session RPE" in item["detail"]
