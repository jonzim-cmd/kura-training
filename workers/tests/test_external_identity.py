from __future__ import annotations

from copy import deepcopy

from kura_workers.external_activity_contract import (
    CONTRACT_VERSION_V1,
    validate_external_activity_contract_v1,
)
from kura_workers.external_identity import (
    ExistingImportRecord,
    activity_payload_fingerprint,
    build_external_idempotency_key,
    evaluate_duplicate_policy,
    source_identity_key,
)


def _contract_payload(*, version: str | None = "1", reps: int = 8) -> dict:
    return {
        "contract_version": CONTRACT_VERSION_V1,
        "source": {
            "provider": "garmin",
            "provider_user_id": "usr-42",
            "external_activity_id": "activity-777",
            "external_event_version": version,
            "ingestion_method": "file_import",
            "raw_payload_ref": "local://run.fit",
            "imported_at": "2026-02-12T08:31:00+00:00",
        },
        "workout": {
            "workout_type": "ride",
            "duration_seconds": 3600,
            "distance_meters": 24000,
        },
        "session": {
            "started_at": "2026-02-12T07:00:00+00:00",
            "ended_at": "2026-02-12T08:00:00+00:00",
            "timezone": "UTC",
            "local_date": "2026-02-12",
            "local_week": "2026-W07",
        },
        "sets": [
            {
                "sequence": 1,
                "exercise": "ride_interval",
                "reps": reps,
                "rpe": 7,
            }
        ],
        "provenance": {
            "mapping_version": "garmin-v1",
            "mapped_at": "2026-02-12T08:32:00+00:00",
            "source_confidence": 1.0,
            "field_provenance": {},
            "unsupported_fields": [],
            "warnings": [],
        },
    }


def _contract(*, version: str | None = "1", reps: int = 8):
    return validate_external_activity_contract_v1(
        _contract_payload(version=version, reps=reps)
    )


def test_source_identity_key_is_stable_for_same_provider_tuple():
    key_a = source_identity_key(_contract(version="1", reps=8))
    key_b = source_identity_key(_contract(version="2", reps=10))

    assert key_a == key_b


def test_external_idempotency_key_is_replay_stable_across_import_timestamps():
    contract_a = _contract(version="7", reps=8)
    payload_b = _contract_payload(version="7", reps=8)
    payload_b["source"]["imported_at"] = "2026-02-12T10:10:00+00:00"
    payload_b["provenance"]["mapped_at"] = "2026-02-12T10:11:00+00:00"
    contract_b = validate_external_activity_contract_v1(payload_b)

    assert build_external_idempotency_key(contract_a) == build_external_idempotency_key(
        contract_b
    )


def test_external_idempotency_key_changes_for_new_external_event_version():
    key_v1 = build_external_idempotency_key(_contract(version="1", reps=8))
    key_v2 = build_external_idempotency_key(_contract(version="2", reps=8))

    assert key_v1 != key_v2


def test_duplicate_policy_first_import_is_new_activity():
    candidate = _contract(version="1", reps=8)
    result = evaluate_duplicate_policy(
        candidate_version=candidate.source.external_event_version,
        candidate_payload_fingerprint=activity_payload_fingerprint(candidate),
        existing_records=[],
    )

    assert result.decision == "create"
    assert result.outcome == "new_activity"


def test_duplicate_policy_exact_duplicate_is_skipped():
    candidate = _contract(version="1", reps=8)
    fingerprint = activity_payload_fingerprint(candidate)
    result = evaluate_duplicate_policy(
        candidate_version="1",
        candidate_payload_fingerprint=fingerprint,
        existing_records=[
            ExistingImportRecord(
                external_event_version="1",
                payload_fingerprint=fingerprint,
            )
        ],
    )

    assert result.decision == "skip"
    assert result.outcome == "exact_duplicate"


def test_duplicate_policy_same_version_changed_payload_is_conflict():
    candidate = _contract(version="1", reps=9)
    result = evaluate_duplicate_policy(
        candidate_version="1",
        candidate_payload_fingerprint=activity_payload_fingerprint(candidate),
        existing_records=[
            ExistingImportRecord(
                external_event_version="1",
                payload_fingerprint=activity_payload_fingerprint(_contract(version="1", reps=8)),
            )
        ],
    )

    assert result.decision == "reject"
    assert result.outcome == "version_conflict"


def test_duplicate_policy_newer_version_is_update():
    candidate = _contract(version="3", reps=8)
    result = evaluate_duplicate_policy(
        candidate_version="3",
        candidate_payload_fingerprint=activity_payload_fingerprint(candidate),
        existing_records=[
            ExistingImportRecord(external_event_version="1", payload_fingerprint="fp-v1"),
            ExistingImportRecord(external_event_version="2", payload_fingerprint="fp-v2"),
        ],
    )

    assert result.decision == "update"
    assert result.outcome == "version_update"


def test_duplicate_policy_stale_version_is_rejected():
    candidate = _contract(version="1", reps=8)
    result = evaluate_duplicate_policy(
        candidate_version="1",
        candidate_payload_fingerprint=activity_payload_fingerprint(candidate),
        existing_records=[
            ExistingImportRecord(external_event_version="3", payload_fingerprint="fp-v3")
        ],
    )

    assert result.decision == "reject"
    assert result.outcome == "stale_version"


def test_duplicate_policy_without_version_changed_payload_is_partial_overlap():
    candidate_payload = _contract_payload(version=None, reps=8)
    candidate_payload["source"]["external_event_version"] = None
    candidate = validate_external_activity_contract_v1(candidate_payload)

    baseline_payload = deepcopy(candidate_payload)
    baseline_payload["sets"][0]["reps"] = 6
    baseline = validate_external_activity_contract_v1(baseline_payload)

    result = evaluate_duplicate_policy(
        candidate_version=None,
        candidate_payload_fingerprint=activity_payload_fingerprint(candidate),
        existing_records=[
            ExistingImportRecord(
                external_event_version=None,
                payload_fingerprint=activity_payload_fingerprint(baseline),
            )
        ],
    )

    assert result.decision == "reject"
    assert result.outcome == "partial_overlap"
