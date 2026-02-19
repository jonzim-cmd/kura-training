from __future__ import annotations

from pathlib import Path

EVAL_HARNESS = Path("workers/src/kura_workers/eval_harness.py")
NIGHTLY_HANDLER = Path("workers/src/kura_workers/handlers/inference_nightly.py")


def test_eval_event_store_replay_covers_session_correction_external_and_timezone_signals() -> None:
    src = EVAL_HARNESS.read_text(encoding="utf-8")
    assert "external.activity_imported" in src
    assert "session.logged" in src
    assert "set.corrected" in src
    assert "preference.set" in src


def test_eval_readiness_replay_uses_timezone_context_not_fixed_utc() -> None:
    src = EVAL_HARNESS.read_text(encoding="utf-8")
    assert "timezone_pref = _timezone_preference_from_rows(rows)" in src
    assert "build_readiness_daily_scores(rows, timezone_name=timezone_name)" in src


def test_nightly_refit_triggers_cover_session_correction_and_external_import() -> None:
    src = NIGHTLY_HANDLER.read_text(encoding="utf-8")
    assert "session.logged" in src
    assert "set.corrected" in src
    assert "external.activity_imported" in src

