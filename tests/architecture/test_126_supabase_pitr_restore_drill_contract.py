from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PITR_REPORT = REPO_ROOT / "docs" / "reports" / "supabase-pitr-restore-drill-2026-02-16.md"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "supabase-cutover.md"
BASELINE_REPORT = REPO_ROOT / "docs" / "reports" / "supabase-pro-baseline-2026-02-15.md"


def test_pitr_restore_drill_report_contains_restore_command_and_recovery_evidence() -> None:
    src = PITR_REPORT.read_text(encoding="utf-8")

    assert "supabase backups restore" in src
    assert "Started PITR restore" in src
    assert "Measured recovery duration" in src
    assert "Post-Drill Verification" in src
    assert "users = 1" in src
    assert "events = 270" in src


def test_runbook_and_baseline_reference_pitr_success_state() -> None:
    runbook_src = RUNBOOK.read_text(encoding="utf-8")
    baseline_src = BASELINE_REPORT.read_text(encoding="utf-8")

    assert "PITR gate" in runbook_src
    assert "PASS" in runbook_src
    assert "pitr_enabled=true" in runbook_src
    assert "pitr_enabled = true" in baseline_src
    assert "pitr_7" in baseline_src
