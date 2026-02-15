from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "supabase-cutover.md"
MONITOR_REPORT = REPO_ROOT / "docs" / "reports" / "supabase-monitoring-drill-2026-02-15.md"


def test_runbook_declares_required_cutover_monitoring_signals() -> None:
    src = RUNBOOK.read_text(encoding="utf-8")

    assert "Monitoring & Alerts" in src
    assert "Auth failure rate" in src
    assert "DB latency / connection errors" in src
    assert "API 5xx" in src
    assert "Worker dead jobs" in src
    assert "Owner" in src
    assert "Reaction SLA" in src


def test_runbook_includes_post_cutover_checklist_and_comms_templates() -> None:
    src = RUNBOOK.read_text(encoding="utf-8")

    assert "24h Post-Cutover Checklist" in src
    assert "Communication Templates" in src
    assert "Cutover start" in src
    assert "Rollback triggered" in src
    assert "Cutover complete" in src


def test_monitoring_report_records_trigger_evidence_for_all_required_signals() -> None:
    src = MONITOR_REPORT.read_text(encoding="utf-8")

    assert "Drill 1: Auth Failure Rate" in src
    assert "Drill 2: DB Connection Error Signal" in src
    assert "Drill 3: API 5xx Signal" in src
    assert "Drill 4: Worker Dead-Jobs Signal" in src
