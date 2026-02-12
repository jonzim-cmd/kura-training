from __future__ import annotations

import json

import pytest

from kura_workers.external_identity import ExistingImportRecord
from kura_workers.external_import_pipeline import (
    ImportPipelineError,
    build_import_plan,
)


def _fit_payload_json() -> str:
    return json.dumps(
        {
            "sport": "run",
            "start_time": "2026-02-12T06:30:00+00:00",
            "duration_seconds": 1800,
            "distance_meters": 5000,
            "calories_kcal": 420,
            "timezone": "UTC",
        }
    )


def _tcx_payload() -> str:
    return """
    <TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
      <Activities>
        <Activity Sport="Biking">
          <Id>2026-02-12T17:00:00Z</Id>
          <Lap StartTime="2026-02-12T17:00:00Z">
            <TotalTimeSeconds>1200</TotalTimeSeconds>
            <DistanceMeters>10000</DistanceMeters>
            <Calories>310</Calories>
          </Lap>
          <Lap StartTime="2026-02-12T17:20:00Z">
            <TotalTimeSeconds>900</TotalTimeSeconds>
            <DistanceMeters>7000</DistanceMeters>
            <Calories>200</Calories>
          </Lap>
        </Activity>
      </Activities>
    </TrainingCenterDatabase>
    """


def _gpx_payload() -> str:
    return """
    <gpx version="1.1" creator="kura-tests" xmlns="http://www.topografix.com/GPX/1/1">
      <trk>
        <name>Morning Run</name>
        <trkseg>
          <trkpt lat="52.52" lon="13.40">
            <time>2026-02-12T06:00:00Z</time>
          </trkpt>
          <trkpt lat="52.53" lon="13.41">
            <time>2026-02-12T06:25:00Z</time>
          </trkpt>
        </trkseg>
      </trk>
    </gpx>
    """


def test_fit_import_plan_builds_writeable_plan():
    plan = build_import_plan(
        provider="garmin",
        provider_user_id="u-fit",
        external_activity_id="fit-1",
        file_format="fit",
        payload_text=_fit_payload_json(),
        external_event_version="1",
        existing_records=[],
    )

    assert plan.should_write is True
    assert plan.dedup_result.decision == "create"
    assert plan.idempotency_key.startswith("external-import-")


def test_tcx_import_plan_builds_writeable_plan():
    plan = build_import_plan(
        provider="trainingpeaks",
        provider_user_id="u-tcx",
        external_activity_id="tcx-1",
        file_format="tcx",
        payload_text=_tcx_payload(),
        external_event_version="7",
        existing_records=[],
    )

    assert plan.should_write is True
    assert plan.canonical_activity.workout.duration_seconds == 2100
    assert plan.canonical_activity.workout.distance_meters == 17000


def test_gpx_import_plan_builds_writeable_plan():
    plan = build_import_plan(
        provider="strava",
        provider_user_id="u-gpx",
        external_activity_id="gpx-1",
        file_format="gpx",
        payload_text=_gpx_payload(),
        external_event_version="2",
        existing_records=[],
    )

    assert plan.should_write is True
    assert plan.canonical_activity.workout.duration_seconds == 1500


def test_reimport_same_version_and_fingerprint_is_idempotent_skip():
    first = build_import_plan(
        provider="garmin",
        provider_user_id="u-fit",
        external_activity_id="fit-1",
        file_format="fit",
        payload_text=_fit_payload_json(),
        external_event_version="5",
        existing_records=[],
    )
    second = build_import_plan(
        provider="garmin",
        provider_user_id="u-fit",
        external_activity_id="fit-1",
        file_format="fit",
        payload_text=_fit_payload_json(),
        external_event_version="5",
        existing_records=[
            ExistingImportRecord(
                external_event_version="5",
                payload_fingerprint=first.payload_fingerprint,
            )
        ],
    )

    assert second.should_write is False
    assert second.dedup_result.outcome == "exact_duplicate"


def test_invalid_payload_raises_classified_pipeline_error():
    with pytest.raises(ImportPipelineError) as exc:
        build_import_plan(
            provider="garmin",
            provider_user_id="u-fit",
            external_activity_id="fit-bad",
            file_format="fit",
            payload_text="this-is-not-json",
            external_event_version="1",
            existing_records=[],
        )

    assert exc.value.code == "parse_error"
