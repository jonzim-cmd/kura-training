from __future__ import annotations

from pathlib import Path

from kura_workers.inference_event_registry import CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKFILL_SCRIPT = REPO_ROOT / "scripts" / "backfill-capability-estimation.sh"
BACKFILL_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "capability-estimation-backfill.md"
INFERENCE_NIGHTLY = REPO_ROOT / "workers" / "src" / "kura_workers" / "handlers" / "inference_nightly.py"


def test_capability_backfill_event_type_surface_is_pinned() -> None:
    assert CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES == (
        "set.logged",
        "session.logged",
        "set.corrected",
        "external.activity_imported",
    )


def test_capability_backfill_handler_is_registered() -> None:
    src = INFERENCE_NIGHTLY.read_text(encoding="utf-8")
    assert '@register("inference.capability_backfill")' in src


def test_capability_backfill_script_contains_enqueue_and_verification_contract() -> None:
    src = BACKFILL_SCRIPT.read_text(encoding="utf-8")
    assert "inference.capability_backfill" in src
    assert "projection.update" in src
    assert "Coverage check" in src
    assert "Lag check" in src


def test_capability_backfill_runbook_references_script_and_coverage_queries() -> None:
    src = BACKFILL_RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/backfill-capability-estimation.sh" in src
    assert "users_with_all_capability_keys" in src
    assert "avg_projection_age_minutes" in src
