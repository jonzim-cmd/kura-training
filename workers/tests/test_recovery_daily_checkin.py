from __future__ import annotations

from kura_workers.recovery_daily_checkin import normalize_daily_checkin_payload


def test_normalize_structured_payload() -> None:
    payload = {
        "bodyweight_kg": 78.4,
        "sleep_hours": "7.2",
        "soreness": 3,
        "motivation": 8,
        "hrv_rmssd": "62",
        "traveling_yesterday": "no",
        "sick_today": "false",
        "alcohol_last_night": "none",
        "training_yesterday": "hard",
        "supplements": "creatine; magnesium",
        "notes": "slept better",
    }

    normalized = normalize_daily_checkin_payload(payload)
    assert normalized["bodyweight_kg"] == 78.4
    assert normalized["sleep_hours"] == 7.2
    assert normalized["soreness"] == 3.0
    assert normalized["motivation"] == 8.0
    assert normalized["hrv_rmssd"] == 62.0
    assert normalized["traveling_yesterday"] is False
    assert normalized["sick_today"] is False
    assert normalized["alcohol_last_night"] == "none"
    assert normalized["training_yesterday"] == "hard"
    assert normalized["supplements"] == ["creatine", "magnesium"]
    assert normalized["notes"] == "slept better"
    assert normalized["quality_flags"] == []


def test_normalize_compact_positional_payload() -> None:
    normalized = normalize_daily_checkin_payload(
        {"compact_input": "78.4,7.2,3,8,62,no,no,0"}
    )

    assert normalized["parsed_from_compact"] is True
    assert normalized["compact_input_mode"] == "positional"
    assert normalized["bodyweight_kg"] == 78.4
    assert normalized["sleep_hours"] == 7.2
    assert normalized["soreness"] == 3.0
    assert normalized["motivation"] == 8.0
    assert normalized["hrv_rmssd"] == 62.0
    assert normalized["sick_today"] is False
    assert normalized["traveling_yesterday"] is False
    assert normalized["alcohol_last_night"] == "none"


def test_normalize_compact_key_value_with_spaces() -> None:
    normalized = normalize_daily_checkin_payload(
        {"compact_input": "sleep=7.2 soreness=3 motivation=8 hrv=62"}
    )

    assert normalized["parsed_from_compact"] is True
    assert normalized["compact_input_mode"] == "key_value"
    assert normalized["sleep_hours"] == 7.2
    assert normalized["soreness"] == 3.0
    assert normalized["motivation"] == 8.0
    assert normalized["hrv_rmssd"] == 62.0


def test_normalize_compact_pair_tokens_payload() -> None:
    normalized = normalize_daily_checkin_payload(
        {"compact_input": "bw 78.4 sl 7.1 sor 2 mot 9"}
    )

    assert normalized["parsed_from_compact"] is True
    assert normalized["compact_input_mode"] == "pair_tokens"
    assert normalized["bodyweight_kg"] == 78.4
    assert normalized["sleep_hours"] == 7.1
    assert normalized["soreness"] == 2.0
    assert normalized["motivation"] == 9.0


def test_invalid_values_produce_quality_flags() -> None:
    normalized = normalize_daily_checkin_payload(
        {"compact_input": "sleep=abc soreness=eleven motivation=yes"}
    )
    assert "invalid_sleep_hours" in normalized["quality_flags"]
    assert "invalid_soreness" in normalized["quality_flags"]
    assert "invalid_motivation" in normalized["quality_flags"]
