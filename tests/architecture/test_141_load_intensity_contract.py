from __future__ import annotations

from kura_workers.training_load_calibration_v1 import (
    CALIBRATED_PARAMETER_VERSION,
    calibration_profile_for_version,
    compute_row_load_components_v2,
)


def _profile() -> dict:
    return calibration_profile_for_version(CALIBRATED_PARAMETER_VERSION)


def test_load_contract_scales_with_intensity_for_same_external_dose() -> None:
    profile = _profile()

    low_intensity = compute_row_load_components_v2(
        data={
            "duration_seconds": 1800,
            "distance_meters": 5000,
            "power_watt": 160,
            "ftp_watt": 300,
        },
        profile=profile,
    )
    high_intensity = compute_row_load_components_v2(
        data={
            "duration_seconds": 1800,
            "distance_meters": 5000,
            "power_watt": 285,
            "ftp_watt": 300,
        },
        profile=profile,
    )

    assert round(low_intensity["external_dose"], 6) == round(
        high_intensity["external_dose"], 6
    )
    assert high_intensity["internal_response"] > low_intensity["internal_response"]
    assert high_intensity["load_score"] > low_intensity["load_score"]


def test_load_contract_has_explicit_intensity_fallback_and_uncertainty_gradient() -> None:
    profile = _profile()

    power = compute_row_load_components_v2(
        data={
            "duration_seconds": 1500,
            "power_watt": 250,
            "ftp_watt": 300,
        },
        profile=profile,
    )
    heart_rate = compute_row_load_components_v2(
        data={
            "duration_seconds": 1500,
            "heart_rate_avg": 168,
            "heart_rate_max": 190,
        },
        profile=profile,
    )
    rpe = compute_row_load_components_v2(
        data={
            "duration_seconds": 1500,
            "rpe": 8,
        },
        profile=profile,
    )
    prior = compute_row_load_components_v2(
        data={
            "duration_seconds": 1500,
            "block_type": "continuous_endurance",
        },
        profile=profile,
    )

    assert power["internal_response_source"] == "power_ratio"
    assert heart_rate["internal_response_source"] == "hr_ratio"
    assert rpe["internal_response_source"] == "rpe"
    assert prior["internal_response_source"] == "modality_prior"

    assert power["uncertainty"] < heart_rate["uncertainty"]
    assert heart_rate["uncertainty"] < rpe["uncertainty"]
    assert rpe["uncertainty"] < prior["uncertainty"]

