from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


def test_protocol_vs_integrity_split_contract() -> None:
    from kura_workers.handlers.quality_health import _compute_save_claim_slo

    now = datetime.now(tz=timezone.utc)
    rows = [
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(minutes=1),
            "data": {
                "mismatch_detected": True,
                "allow_saved_claim": False,
                "mismatch_severity": "critical",
                "mismatch_weight": 1.0,
                "mismatch_domain": "save_echo",
            },
        },
        {
            "event_type": "quality.save_claim.checked",
            "timestamp": now - timedelta(minutes=2),
            "data": {
                "mismatch_detected": True,
                "allow_saved_claim": False,
                "mismatch_severity": "info",
                "mismatch_weight": 0.1,
                "mismatch_domain": "protocol",
            },
        },
    ]

    result = _compute_save_claim_slo(rows, now - timedelta(days=7))
    assert result["weighted_mismatch_sum"] == 1.1
    assert result["integrity_weighted_sum"] == 1.0
    assert result["protocol_friction_weighted_sum"] == 0.1
    assert result["value"] == 50.0
    assert result["protocol_friction_rate_pct"] == 5.0
    assert result["domain_breakdown"]["save_echo"] == 1
    assert result["domain_breakdown"]["protocol"] == 1
