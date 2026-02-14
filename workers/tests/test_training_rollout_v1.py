from __future__ import annotations

from kura_workers.training_rollout_v1 import (
    FEATURE_FLAG_TRAINING_LOAD_V2,
    confidence_band,
    is_training_load_v2_enabled,
    rollout_contract_v1,
)


def test_confidence_band_thresholds() -> None:
    assert confidence_band(0.4) == "low"
    assert confidence_band(0.6) == "medium"
    assert confidence_band(0.9) == "high"


def test_training_load_v2_flag_defaults_on(monkeypatch) -> None:
    monkeypatch.delenv(FEATURE_FLAG_TRAINING_LOAD_V2, raising=False)
    assert is_training_load_v2_enabled() is True


def test_training_load_v2_flag_parses_false(monkeypatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG_TRAINING_LOAD_V2, "false")
    assert is_training_load_v2_enabled() is False


def test_rollout_contract_contains_required_monitoring_metrics() -> None:
    contract = rollout_contract_v1()
    assert contract["policy_version"] == "training_rollout.v1"
    assert set(contract["qa_matrix"]) == {
        "strength_manual_only",
        "sprint_interval_manual",
        "endurance_sensor_rich",
        "hybrid_strength_endurance",
        "low_data_user",
    }
    assert FEATURE_FLAG_TRAINING_LOAD_V2 == contract["feature_flags"]["training_load_v2"]["env_var"]
    assert {
        "external_import_parse_fail_rate_pct",
        "session_missing_anchor_rate_pct",
        "session_confidence_distribution",
    } <= set(contract["monitoring"]["metrics"])
