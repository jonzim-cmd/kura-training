from __future__ import annotations

from pathlib import Path

from kura_workers.capability_estimation_runtime import (
    STATUS_INSUFFICIENT_DATA,
    build_capability_envelope,
    build_insufficient_envelope,
    data_sufficiency_block,
)


def _assert_required_fields(payload: dict) -> None:
    required = {
        "schema_version",
        "capability",
        "status",
        "estimate",
        "confidence",
        "data_sufficiency",
        "caveats",
        "recommended_next_observations",
        "model_version",
        "generated_at",
    }
    assert required <= set(payload.keys())
    assert set(payload["estimate"].keys()) == {"mean", "interval"}
    assert set(payload["data_sufficiency"].keys()) == {
        "required_observations",
        "observed_observations",
        "uncertainty_reason_codes",
        "recommended_next_observations",
    }


def test_capability_output_contract_is_parser_stable_for_ok_and_insufficient() -> None:
    ok_payload = build_capability_envelope(
        capability="strength_1rm",
        estimate_mean=145.0,
        estimate_interval=[140.0, 150.0],
        status="ok",
        confidence=0.82,
        data_sufficiency=data_sufficiency_block(
            required_observations=6,
            observed_observations=8,
            uncertainty_reason_codes=[],
            recommended_next_observations=[],
        ),
        model_version="capability_estimation.v1",
    )
    insufficient_payload = build_insufficient_envelope(
        capability="sprint_max_speed",
        required_observations=6,
        observed_observations=2,
        model_version="capability_estimation.v1",
    )

    _assert_required_fields(ok_payload)
    _assert_required_fields(insufficient_payload)
    assert insufficient_payload["status"] == STATUS_INSUFFICIENT_DATA


def test_capability_handler_is_registered_in_handler_bootstrap() -> None:
    handlers_init = Path("workers/src/kura_workers/handlers/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "from . import capability_estimation" in handlers_init
