from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


def _row(event_type: str, data: dict[str, object], ts: datetime) -> dict[str, object]:
    return {"event_type": event_type, "timestamp": ts, "data": data}


def test_posterior_contract_low_sample_mismatch_does_not_force_degraded() -> None:
    from kura_workers.handlers.quality_health import _compute_save_claim_slo

    now = datetime.now(tz=timezone.utc)
    rows = []
    for idx in range(6):
        mismatch = idx < 2
        rows.append(
            _row(
                "quality.save_claim.checked",
                {
                    "mismatch_detected": mismatch,
                    "allow_saved_claim": not mismatch,
                    "mismatch_severity": "critical" if mismatch else "none",
                    "mismatch_weight": 1.0 if mismatch else 0.0,
                    "mismatch_domain": "save_echo" if mismatch else "none",
                },
                now - timedelta(minutes=idx),
            )
        )

    result = _compute_save_claim_slo(rows, now - timedelta(days=7))
    assert result["decision_mode"] == "posterior_risk_beta_approx"
    assert result["sample_count"] == 6
    assert result["mismatch_count"] == 2
    assert result["status"] != "degraded"
    assert result["posterior_prob_gt_degraded"] < 0.9


def test_posterior_contract_persistent_critical_mismatch_degrades() -> None:
    from kura_workers.handlers.quality_health import _compute_save_claim_slo

    now = datetime.now(tz=timezone.utc)
    rows = []
    for idx in range(20):
        mismatch = idx < 8
        rows.append(
            _row(
                "quality.save_claim.checked",
                {
                    "mismatch_detected": mismatch,
                    "allow_saved_claim": not mismatch,
                    "mismatch_severity": "critical" if mismatch else "none",
                    "mismatch_weight": 1.0 if mismatch else 0.0,
                    "mismatch_domain": "save_echo" if mismatch else "none",
                },
                now - timedelta(minutes=idx),
            )
        )

    result = _compute_save_claim_slo(rows, now - timedelta(days=7))
    assert result["sample_count"] == 20
    assert result["value"] == 40.0
    assert result["posterior_prob_gt_degraded"] >= 0.9
    assert result["status"] == "degraded"
