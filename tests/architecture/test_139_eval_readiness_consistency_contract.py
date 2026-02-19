from __future__ import annotations

from pathlib import Path

EVAL_HARNESS = Path("workers/src/kura_workers/eval_harness.py")


def test_eval_contract_uses_shared_readiness_signal_builder() -> None:
    src = EVAL_HARNESS.read_text(encoding="utf-8")
    assert "from .readiness_signals import build_readiness_daily_scores" in src
    assert "build_readiness_daily_scores(rows, timezone_name=timezone_name)" in src


def test_eval_contract_replays_readiness_with_calendar_day_offsets() -> None:
    src = EVAL_HARNESS.read_text(encoding="utf-8")
    assert "subset_offsets" in src
    assert "day_offsets=subset_offsets" in src
