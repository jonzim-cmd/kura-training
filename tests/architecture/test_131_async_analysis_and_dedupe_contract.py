from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROUTE = REPO_ROOT / "api" / "src" / "routes" / "analysis_jobs.rs"
MCP_RUNTIME = REPO_ROOT / "mcp-runtime" / "src" / "lib.rs"
WORKER_HANDLER = REPO_ROOT / "workers" / "src" / "kura_workers" / "handlers" / "deep_analysis.py"
MIGRATION = REPO_ROOT / "migrations" / "20260302000001_analysis_jobs.sql"


def test_analysis_api_contract_exposes_async_create_and_get() -> None:
    """Why: async orchestration must remain explicit even after context loss."""
    src = ANALYSIS_ROUTE.read_text(encoding="utf-8")
    assert "route(\"/v1/analysis/jobs\", post(create_analysis_job))" in src
    assert "route(\"/v1/analysis/jobs/{job_id}\", get(get_analysis_job))" in src
    assert "pub struct CreateAnalysisJobRequest" in src
    assert "pub struct AnalysisJobStatusResponse" in src
    assert "analysis.deep_insight" in src


def test_mcp_runtime_contract_exposes_analysis_tools_and_burst_dedupe() -> None:
    """Why: external agents use MCP; dedupe must be backend-side and provider-agnostic."""
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "kura_analysis_job_create" in src
    assert "kura_analysis_job_get" in src
    assert "TOOL_CALL_DEDUPE_WINDOW_MS" in src
    assert "is_tool_call_dedupe_eligible" in src
    assert "burst_retry_coalesced" in src
    assert "store_tool_call_dedupe_entry" in src
    assert "get_tool_call_dedupe_entry" in src


def test_worker_contract_registers_deep_analysis_job_handler() -> None:
    """Why: API create/get contract is only valid if worker execution path is registered."""
    src = WORKER_HANDLER.read_text(encoding="utf-8")
    assert "@register(\"analysis.deep_insight\")" in src
    assert "ANALYSIS_RESULT_SCHEMA_VERSION = \"deep_analysis_result.v1\"" in src
    assert "status = 'completed'" in src
    assert "status = 'failed'" in src


def test_analysis_migration_is_additive_and_backcompat_safe() -> None:
    """Why: rollout must preserve existing data and support post-compaction recovery."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS analysis_jobs" in src
    assert "CHECK (status IN ('queued', 'processing', 'completed', 'failed'))" in src
    assert "ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY;" in src
    assert "analysis_jobs_user_isolation" in src
    assert "GRANT SELECT ON analysis_jobs TO app_reader;" in src
    assert "GRANT SELECT, INSERT, UPDATE ON analysis_jobs TO app_worker;" in src
