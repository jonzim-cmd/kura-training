from __future__ import annotations

from pathlib import Path

READINESS_HANDLER = Path("workers/src/kura_workers/handlers/readiness_inference.py")
CAUSAL_HANDLER = Path("workers/src/kura_workers/handlers/causal_inference.py")
STRENGTH_HANDLER = Path("workers/src/kura_workers/handlers/strength_inference.py")
READINESS_SIGNALS = Path("workers/src/kura_workers/readiness_signals.py")


def test_readiness_contract_includes_session_and_correction_signal_paths() -> None:
    src = READINESS_HANDLER.read_text(encoding="utf-8")
    assert "session.logged" in src
    assert "set.corrected" in src


def test_causal_contract_includes_session_correction_and_alias_signal_paths() -> None:
    src = CAUSAL_HANDLER.read_text(encoding="utf-8")
    assert "session.logged" in src
    assert "set.corrected" in src
    assert "exercise.alias_created" in src


def test_strength_contract_includes_session_and_correction_signal_paths() -> None:
    src = STRENGTH_HANDLER.read_text(encoding="utf-8")
    assert "session.logged" in src
    assert "set.corrected" in src


def test_readiness_signal_builder_uses_session_expansion_and_correction_overlay() -> None:
    src = READINESS_SIGNALS.read_text(encoding="utf-8")
    assert "expand_session_logged_rows" in src
    assert "apply_set_correction_chain" in src
    assert "extract_backfilled_set_event_ids" in src

