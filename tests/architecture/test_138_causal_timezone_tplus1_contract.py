from __future__ import annotations

from pathlib import Path

CAUSAL_HANDLER = Path("workers/src/kura_workers/handlers/causal_inference.py")


def test_causal_contract_uses_user_timezone_day_grouping() -> None:
    src = CAUSAL_HANDLER.read_text(encoding="utf-8")
    assert "load_timezone_preference" in src
    assert "resolve_timezone_context" in src
    assert "normalize_temporal_point" in src
    assert "timezone_context" in src


def test_causal_contract_enforces_true_calendar_t_plus_1_windows() -> None:
    src = CAUSAL_HANDLER.read_text(encoding="utf-8")
    assert "next_day_key = current_day + timedelta(days=1)" in src
    assert "if next_day_key not in context_by_date" in src
    assert "next_day = context_by_date[next_day_key]" in src
