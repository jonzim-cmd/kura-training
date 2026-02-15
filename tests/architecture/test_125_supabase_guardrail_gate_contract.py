from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_REPORT = REPO_ROOT / "docs" / "reports" / "supabase-pro-baseline-2026-02-15.md"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "supabase-cutover.md"
DRY_RUN_REPORT = REPO_ROOT / "docs" / "reports" / "supabase-dry-run-2026-02-15.md"


def test_baseline_report_captures_billing_plan_and_guardrail_status() -> None:
    src = BASELINE_REPORT.read_text(encoding="utf-8")

    assert "Organization Billing Status" in src
    assert "Organization plan" in src
    assert "Backup / PITR Status" in src
    assert "Spend Guardrails API Coverage" in src
    assert "Required Manual Actions Before Public Launch" in src


def test_runbook_go_no_go_table_keeps_pitr_and_spend_as_explicit_gates() -> None:
    src = RUNBOOK.read_text(encoding="utf-8")

    assert "Go/No-Go Gates" in src
    assert "PITR gate" in src
    assert "Spend guardrail gate" in src
    assert "NO-GO" in src


def test_dry_run_report_links_guardrail_blockers_to_baseline() -> None:
    src = DRY_RUN_REPORT.read_text(encoding="utf-8")

    assert "Remaining Launch Blockers" in src
    assert "pitr_enabled=false" in src
    assert "billing plan" in src
    assert "`free`" in src
    assert "supabase-pro-baseline-2026-02-15.md" in src
