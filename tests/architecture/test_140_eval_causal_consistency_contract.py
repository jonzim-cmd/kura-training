from __future__ import annotations

from pathlib import Path

EVAL_HARNESS = Path("workers/src/kura_workers/eval_harness.py")


def test_eval_causal_contract_uses_shared_readiness_and_timezone_normalization() -> None:
    src = EVAL_HARNESS.read_text(encoding="utf-8")
    assert "build_readiness_daily_scores(rows, timezone_name=timezone_name)" in src
    assert "resolve_timezone_context" in src
    assert "normalize_temporal_point" in src
    assert '"timezone_context": timezone_context' in src


def test_eval_causal_contract_enforces_calendar_t_plus_1_and_quality_metadata() -> None:
    src = EVAL_HARNESS.read_text(encoding="utf-8")
    assert "next_day_key = current_day + timedelta(days=1)" in src
    assert "if next_day_key not in context_by_date" in src
    assert '"temporal_conflicts": temporal_conflicts' in src
    assert '"missing_signal_counts": readiness_signals.get("missing_signal_counts") or {}' in src
