from __future__ import annotations

from pathlib import Path

READINESS_HANDLER = Path("workers/src/kura_workers/handlers/readiness_inference.py")
READINESS_SIGNALS = Path("workers/src/kura_workers/readiness_signals.py")


def test_readiness_contract_consumes_external_imported_load_signals() -> None:
    handler_src = READINESS_HANDLER.read_text(encoding="utf-8")
    assert "external.activity_imported" in handler_src
    assert "build_readiness_daily_scores" in handler_src


def test_readiness_contract_uses_shared_modality_aware_load_builder() -> None:
    signals_src = READINESS_SIGNALS.read_text(encoding="utf-8")
    assert "compute_row_load_components_v2" in signals_src
    assert "_iter_event_load_rows" in signals_src
    assert "duration_seconds" in signals_src
    assert "distance_meters" in signals_src
    assert "contacts" in signals_src


def test_readiness_contract_removes_legacy_fixed_default_scoring_path() -> None:
    handler_src = READINESS_HANDLER.read_text(encoding="utf-8")
    assert 'values.get("sleep_hours", 6.5)' not in handler_src
    assert 'values.get("energy", 6.0)' not in handler_src
    assert "missing_signal_counts" in handler_src
