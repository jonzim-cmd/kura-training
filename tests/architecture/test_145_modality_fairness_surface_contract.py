from __future__ import annotations

from pathlib import Path

from kura_workers.external_import_mapping_v2 import import_mapping_contract_v2

TRAINING_TIMELINE = Path("workers/src/kura_workers/handlers/training_timeline.py")
USER_PROFILE = Path("workers/src/kura_workers/handlers/user_profile.py")


def test_training_timeline_contract_exposes_modality_neutral_load_summary_fields() -> None:
    src = TRAINING_TIMELINE.read_text(encoding="utf-8")
    assert '"total_load_score": "number"' in src
    assert '"total_load_confidence": "number [0,1]"' in src
    assert "load_v2_overview" in src


def test_user_profile_coverage_tracks_training_activity_not_set_only_range() -> None:
    src = USER_PROFILE.read_text(encoding="utf-8")
    assert "training_activity_range" in src
    assert "set_logged_range" not in src


def test_modality_fairness_surface_keeps_open_set_unknown_visible() -> None:
    contract = import_mapping_contract_v2()
    assert "unknown" in contract["modalities"]
    assert any("Open-set routing" in rule for rule in contract["rules"])
