from __future__ import annotations

import sys
from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


# ── Python-side contract assertions ──────────────────────────────────


def test_quality_health_uses_weighted_mismatch_sum() -> None:
    """_compute_save_claim_slo must return weighted_mismatch_sum and severity_breakdown."""
    from datetime import datetime, timedelta, timezone

    from kura_workers.handlers.quality_health import _compute_save_claim_slo

    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(days=7)

    # One critical mismatch (weight=1.0) and one info mismatch (weight=0.1)
    rows = [
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_detected": True,
                "allow_saved_claim": False,
                "mismatch_severity": "critical",
                "mismatch_weight": 1.0,
            },
        },
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=2),
            "data": {
                "mismatch_detected": True,
                "allow_saved_claim": False,
                "mismatch_severity": "info",
                "mismatch_weight": 0.1,
            },
        },
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=3),
            "data": {
                "mismatch_detected": False,
                "allow_saved_claim": True,
                "mismatch_severity": "none",
                "mismatch_weight": 0.0,
            },
        },
    ]

    result = _compute_save_claim_slo(rows, window_start)
    assert "weighted_mismatch_sum" in result
    assert "severity_breakdown" in result
    assert result["weighted_mismatch_sum"] == 1.1
    assert result["severity_breakdown"]["critical"] == 1
    assert result["severity_breakdown"]["info"] == 1
    assert result["severity_breakdown"]["none"] == 1
    # weighted rate: 1.1 / 3 * 100 = 36.67%
    assert result["weighted_mismatch_rate_pct"] == 36.67


def test_quality_health_legacy_fallback_for_events_without_severity() -> None:
    """Legacy fallback treats pending readback as protocol friction, not integrity break."""
    from datetime import datetime, timedelta, timezone

    from kura_workers.handlers.quality_health import _compute_save_claim_slo

    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(days=7)

    rows = [
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=1),
            "data": {
                "mismatch_detected": True,
                "allow_saved_claim": False,
                "verification_status": "pending",
                "claim_status": "pending",
                "uncertainty_markers": ["read_after_write_unverified"],
                # No mismatch_severity or mismatch_weight fields
            },
        },
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(hours=2),
            "data": {
                "allow_saved_claim": True,
            },
        },
    ]

    result = _compute_save_claim_slo(rows, window_start)
    assert result["weighted_mismatch_sum"] == 0.1
    assert result["severity_breakdown"]["info"] == 1
    assert result["severity_breakdown"]["none"] == 1
    assert result["value"] == 0.0
    assert result["protocol_friction_rate_pct"] == 5.0
    assert result["mismatch_count"] == 1


def test_issue_clustering_severity_modifier_reduces_info_weight() -> None:
    """Info-level save_claim_mismatch_attempt signals should have reduced severity weight."""
    from kura_workers.issue_clustering import _severity_weight

    # Without mismatch_severity (legacy): base weight 1.0
    base = _severity_weight("save_claim_mismatch_attempt", "high")
    # With info severity: weight * 0.1
    info = _severity_weight(
        "save_claim_mismatch_attempt",
        "high",
        {"mismatch_severity": "info"},
    )
    # With critical severity: weight * 1.0 (unchanged)
    critical = _severity_weight(
        "save_claim_mismatch_attempt",
        "high",
        {"mismatch_severity": "critical"},
    )

    assert critical == base, "critical severity should equal base weight"
    assert info < base * 0.2, f"info severity {info} should be much less than base {base}"


# ── Rust runtime contract tests (delegated) ──────────────────────────


def test_save_claim_mismatch_severity_contract_critical_when_echo_missing() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::save_claim_mismatch_severity_contract_critical_when_echo_missing"
    )


def test_save_claim_mismatch_severity_contract_info_when_only_protocol_detail_missing() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::save_claim_mismatch_severity_contract_info_when_only_protocol_detail_missing"
    )


def test_save_claim_mismatch_severity_contract_backcompat_defaults_for_legacy_payload() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::save_claim_mismatch_severity_contract_backcompat_defaults_for_legacy_payload"
    )
