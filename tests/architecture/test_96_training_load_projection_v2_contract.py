from __future__ import annotations

from kura_workers.system_config import _get_conventions
from kura_workers.training_load_v2 import (
    accumulate_row_load_v2,
    finalize_session_load_v2,
    init_session_load_v2,
    load_projection_contract_v2,
)


def test_training_load_projection_v2_is_declared_in_system_conventions() -> None:
    conventions = _get_conventions()
    projection = conventions["training_load_projection_v2"]
    assert projection["projection_type"] == "training_timeline"
    assert projection["contract"]["schema_version"] == "training_load.v2"

    rules_text = " ".join(projection["rules"]).lower()
    assert "manual-only logging remains valid" in rules_text
    assert "confidence degrades" in rules_text


def test_training_load_projection_v2_supports_sparse_manual_data() -> None:
    session = init_session_load_v2()
    accumulate_row_load_v2(
        session,
        data={"weight_kg": 90, "reps": 5},
        source_type="manual",
    )
    finalized = finalize_session_load_v2(session)
    assert finalized["global"]["load_score"] > 0
    assert finalized["global"]["confidence"] >= 0.6
    assert finalized["global"]["analysis_tier"] in {
        "analysis_basic",
        "analysis_advanced",
        "log_valid",
    }


def test_training_load_contract_v2_has_modality_and_tier_registry() -> None:
    contract = load_projection_contract_v2()
    assert set(contract["analysis_tiers"]) == {
        "log_valid",
        "analysis_basic",
        "analysis_advanced",
    }
    assert {"strength", "sprint", "endurance", "plyometric", "mixed"} <= set(
        contract["modalities"]
    )
