from __future__ import annotations

import pytest

from kura_workers.external_adapter import (
    ENVELOPE_VERSION_V1,
    DummyExternalAdapter,
    prepare_provider_agnostic_ingestion,
)


def _raw_payload() -> dict:
    return {
        "external_activity_id": "activity-22",
        "external_event_version": "3",
        "workout": {
            "workout_type": "run",
            "duration_seconds": 1800,
            "distance_meters": 5020,
        },
        "session": {
            "started_at": "2026-02-12T06:30:00+00:00",
            "ended_at": "2026-02-12T07:00:00+00:00",
            "timezone": "UTC",
            "local_date": "2026-02-12",
            "local_week": "2026-W07",
        },
        "sets": [
            {"sequence": 1, "exercise": "run_interval", "duration_seconds": 600, "rpe": 6},
            {"sequence": 2, "exercise": "run_interval", "duration_seconds": 600, "rpe": 7},
        ],
    }


def test_dummy_adapter_produces_valid_v1_envelope_for_multiple_providers():
    payload = _raw_payload()
    garmin_adapter = DummyExternalAdapter(provider="garmin")
    strava_adapter = DummyExternalAdapter(provider="strava")

    garmin_env = garmin_adapter.adapt(provider_user_id="u-1", raw_payload=payload)
    strava_env = strava_adapter.adapt(provider_user_id="u-1", raw_payload=payload)

    assert garmin_env.envelope_version == ENVELOPE_VERSION_V1
    assert garmin_env.validation_report.valid is True
    assert garmin_env.canonical_activity is not None
    assert garmin_env.canonical_activity.source.provider == "garmin"
    assert strava_env.canonical_activity is not None
    assert strava_env.canonical_activity.source.provider == "strava"


def test_adapter_emits_validation_report_when_canonical_contract_is_invalid():
    payload = _raw_payload()
    payload["session"]["started_at"] = None
    adapter = DummyExternalAdapter(provider="trainingpeaks")

    envelope = adapter.adapt(provider_user_id="u-2", raw_payload=payload)

    assert envelope.validation_report.valid is False
    assert envelope.canonical_activity is None
    assert envelope.validation_report.errors
    assert envelope.validation_report.errors[0].docs_hint is not None


def test_prepare_provider_agnostic_ingestion_returns_deterministic_write_inputs():
    adapter = DummyExternalAdapter(provider="garmin")
    envelope = adapter.adapt(provider_user_id="u-1", raw_payload=_raw_payload())

    prepared = prepare_provider_agnostic_ingestion(envelope)

    assert prepared.provider == "garmin"
    assert prepared.source_identity_key.startswith("external-activity-")
    assert prepared.idempotency_key.startswith("external-import-")
    assert prepared.payload_fingerprint


def test_prepare_provider_agnostic_ingestion_rejects_invalid_envelope():
    payload = _raw_payload()
    payload["workout"]["workout_type"] = ""
    adapter = DummyExternalAdapter(provider="garmin")
    envelope = adapter.adapt(provider_user_id="u-1", raw_payload=payload)

    with pytest.raises(ValueError, match="Envelope is not valid"):
        prepare_provider_agnostic_ingestion(envelope)
