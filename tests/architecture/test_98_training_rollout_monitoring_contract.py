from __future__ import annotations

from kura_workers.handlers.quality_health import _evaluate_read_only_invariants
from kura_workers.system_config import _get_conventions


def _row(event_type: str, data: dict) -> dict:
    return {"event_type": event_type, "data": data}


def test_training_rollout_guard_contract_declares_qa_flag_and_monitoring() -> None:
    conventions = _get_conventions()
    rollout = conventions["training_rollout_guard_v1"]["contract"]

    assert "strength_manual_only" in rollout["qa_matrix"]
    assert "training_load_v2" in rollout["feature_flags"]
    assert {
        "external_import_parse_fail_rate_pct",
        "session_missing_anchor_rate_pct",
        "session_confidence_distribution",
    } <= set(rollout["monitoring"]["metrics"])


def test_quality_health_exposes_rollout_monitoring_metrics() -> None:
    rows = [
        _row(
            "session.logged",
            {
                "contract_version": "session.logged.v1",
                "session_meta": {"sport": "running"},
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 6,
                        },
                    }
                ],
                "provenance": {"source_type": "manual"},
            },
        ),
        _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
        _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
    ]
    import_jobs = [
        {"status": "failed", "error_code": "parse_error", "receipt": {}},
        {"status": "completed", "error_code": None, "receipt": {}},
    ]
    _issues, metrics = _evaluate_read_only_invariants(
        rows,
        alias_map={},
        import_job_rows=import_jobs,
    )

    assert metrics["external_import_parse_fail_rate_pct"] == 50.0
    assert metrics["session_missing_anchor_rate_pct"] == 100.0
    distribution = metrics["session_confidence_distribution"]
    assert set(distribution) == {"low", "medium", "high"}
    assert metrics["session_error_code_counts"]["session.logged.anchor.missing"] >= 1
