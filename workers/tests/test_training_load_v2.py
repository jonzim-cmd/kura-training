from __future__ import annotations

from kura_workers.training_load_v2 import (
    accumulate_row_load_v2,
    finalize_session_load_v2,
    infer_row_modality,
    init_session_load_v2,
    load_projection_contract_v2,
    summarize_timeline_load_v2,
)


def test_infer_row_modality_prefers_block_type_mapping() -> None:
    assert infer_row_modality({"block_type": "strength_set"}) == "strength"
    assert infer_row_modality({"block_type": "interval_endurance"}) == "endurance"
    assert infer_row_modality({"block_type": "sprint_accel_maxv"}) == "sprint"
    assert infer_row_modality({"block_type": "plyometric_reactive"}) == "plyometric"


def test_infer_row_modality_uses_exercise_metadata_before_distance_fallback() -> None:
    assert infer_row_modality({"exercise_id": "sprint", "distance_meters": 25}) == "sprint"
    assert (
        infer_row_modality({"exercise_id": "broad_jump_triple", "distance_meters": 8.2})
        == "plyometric"
    )
    assert infer_row_modality({"exercise_id": "approach_vertical_jump"}) == "plyometric"


def test_manual_strength_row_has_valid_load_and_confidence_without_hr() -> None:
    session = init_session_load_v2()
    accumulate_row_load_v2(
        session,
        data={"exercise_id": "barbell_back_squat", "weight_kg": 100, "reps": 5},
        source_type="manual",
    )
    finalized = finalize_session_load_v2(session)

    assert isinstance(finalized["parameter_version"], str)
    assert finalized["global"]["load_score"] > 0
    assert finalized["global"]["confidence"] >= 0.6
    assert finalized["global"]["analysis_tier"] in {
        "log_valid",
        "analysis_basic",
        "analysis_advanced",
    }
    assert finalized["modalities"]["strength"]["rows"] == 1


def test_endurance_row_without_sensor_streams_still_analysis_basic() -> None:
    session = init_session_load_v2()
    accumulate_row_load_v2(
        session,
        data={"block_type": "interval_endurance", "duration_seconds": 1200, "distance_meters": 4000},
        source_type="session_logged",
        session_confidence_hint=0.7,
    )
    finalized = finalize_session_load_v2(session)

    assert finalized["modalities"]["endurance"]["rows"] == 1
    assert finalized["global"]["load_score"] > 0
    assert finalized["global"]["confidence"] >= 0.6
    assert finalized["global"]["confidence_band"] in {"medium", "high"}


def test_distance_rows_route_to_sprint_and_plyometric_buckets_when_exercise_ids_are_known() -> None:
    session = init_session_load_v2()
    accumulate_row_load_v2(
        session,
        data={"exercise_id": "sprint", "distance_meters": 25, "rpe": 9},
        source_type="session_logged",
    )
    accumulate_row_load_v2(
        session,
        data={"exercise_id": "broad_jump_triple", "distance_meters": 8.2, "rpe": 8},
        source_type="session_logged",
    )
    finalized = finalize_session_load_v2(session)

    assert finalized["modalities"]["sprint"]["rows"] == 1
    assert finalized["modalities"]["plyometric"]["rows"] == 1
    assert finalized["modalities"]["endurance"]["rows"] == 0
    assert finalized["global"]["unknown_distance_exercise"]["rows"] == 0


def test_unknown_distance_exercise_ids_are_exposed_in_diagnostics() -> None:
    session = init_session_load_v2()
    accumulate_row_load_v2(
        session,
        data={"exercise_id": "school_track_drill_unknown", "distance_meters": 60, "rpe": 7},
        source_type="session_logged",
    )
    finalized = finalize_session_load_v2(session)

    assert finalized["modalities"]["endurance"]["rows"] == 1
    assert finalized["global"]["unknown_distance_exercise"]["rows"] == 1
    assert (
        finalized["global"]["unknown_distance_exercise"]["exercise_ids"][
            "school_track_drill_unknown"
        ]
        == 1
    )
    assert finalized["global"]["modality_assignment"]["heuristic_distance_endurance"] == 1


def test_relative_intensity_signal_density_and_confidence_are_persisted() -> None:
    session = init_session_load_v2()
    accumulate_row_load_v2(
        session,
        data={
            "exercise_id": "sprint",
            "distance_meters": 100,
            "relative_intensity": {
                "value_pct": 95.0,
                "reference_type": "mss",
                "reference_value": 9.1,
                "reference_measured_at": "2026-02-10T08:00:00+00:00",
                "reference_confidence": 0.81,
            },
        },
        source_type="session_logged",
    )
    finalized = finalize_session_load_v2(session)

    assert finalized["global"]["signal_density"]["rows_with_relative_intensity"] == 1
    assert finalized["global"]["relative_intensity"]["rows_used"] == 1
    assert finalized["global"]["relative_intensity"]["reference_types"]["mss"] == 1
    assert finalized["global"]["relative_intensity"]["reference_confidence_avg"] == 0.81


def test_timeline_summary_aggregates_modalities_and_global_confidence() -> None:
    session_a = init_session_load_v2()
    accumulate_row_load_v2(
        session_a,
        data={"weight_kg": 100, "reps": 5},
        source_type="manual",
    )
    session_a = finalize_session_load_v2(session_a)

    session_b = init_session_load_v2()
    accumulate_row_load_v2(
        session_b,
        data={"duration_seconds": 1800, "distance_meters": 5000},
        source_type="external_import",
    )
    session_b = finalize_session_load_v2(session_b)

    summary = summarize_timeline_load_v2(
        {
            "s1": {"load_v2": session_a},
            "s2": {"load_v2": session_b},
        }
    )

    assert summary["sessions_total"] == 2
    assert summary["parameter_versions"]
    assert summary["modalities"]["strength"]["rows"] == 1
    assert summary["modalities"]["endurance"]["rows"] == 1
    assert summary["global"]["load_score"] > 0
    assert summary["global"]["confidence"] > 0


def test_load_projection_contract_declares_sparse_data_policy() -> None:
    contract = load_projection_contract_v2()
    assert contract["schema_version"] == "training_load.v2"
    assert {"strength", "sprint", "endurance", "plyometric", "mixed"} <= set(
        contract["modalities"]
    )
    assert contract["dual_load_policy"]["internal_response_resolver_order"][0] == "relative_intensity"
    rules_text = " ".join(contract["rules"]).lower()
    assert "no global hr requirement" in rules_text
