from __future__ import annotations

from pathlib import Path

from kura_workers.inference_event_registry import OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
INFERENCE_NIGHTLY = REPO_ROOT / "workers" / "src" / "kura_workers" / "handlers" / "inference_nightly.py"
BACKFILL_SCRIPT = REPO_ROOT / "scripts" / "backfill-objective-foundation.sh"
BACKFILL_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "objective-foundation-backfill.md"


def test_objective_backfill_event_type_surface_is_pinned() -> None:
    assert OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES == (
        "goal.set",
        "objective.set",
        "objective.updated",
        "objective.archived",
        "advisory.override.recorded",
        "profile.updated",
        "set.logged",
        "session.logged",
        "external.activity_imported",
    )


def test_objective_backfill_handler_is_registered() -> None:
    src = INFERENCE_NIGHTLY.read_text(encoding="utf-8")
    assert '@register("inference.objective_backfill")' in src
    assert "OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES" in src
    assert "_enqueue_projection_updates_for_user_set" in src
    assert "include_all_users" in src
    assert "synthetic_event_type=\"profile.updated\"" in src


def test_objective_backfill_script_contains_enqueue_and_verification_contract() -> None:
    src = BACKFILL_SCRIPT.read_text(encoding="utf-8")
    assert "inference.objective_backfill" in src
    assert "'include_all_users', true" in src
    assert "'user_ids'" in src
    assert "projection.update" in src
    assert "Coverage check" in src
    assert "Lag check" in src
    assert "objective_state" in src
    assert "objective_advisory" in src


def test_objective_backfill_runbook_references_script_and_coverage_queries() -> None:
    src = BACKFILL_RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/backfill-objective-foundation.sh" in src
    assert "users_total" in src
    assert "users_with_objective_signals" in src
    assert "users_missing_objective_surfaces_all_users" in src
    assert "users_missing_objective_surfaces_signal_users" in src
    assert "avg_projection_age_minutes" in src
