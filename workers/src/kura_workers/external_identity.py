"""Source identity, idempotency, and dedup policy for external imports (tm5.3)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .external_activity_contract import CanonicalExternalActivityV1

DedupDecision = Literal["create", "skip", "update", "reject"]
DedupOutcome = Literal[
    "new_activity",
    "exact_duplicate",
    "version_update",
    "stale_version",
    "version_conflict",
    "partial_overlap",
]


@dataclass(frozen=True)
class ExistingImportRecord:
    external_event_version: str | None
    payload_fingerprint: str


@dataclass(frozen=True)
class DedupResult:
    decision: DedupDecision
    outcome: DedupOutcome
    reason: str


def _stable_hash(value: str, size: int = 20) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:size]


def source_identity_seed(contract: CanonicalExternalActivityV1) -> str:
    source = contract.source
    return (
        f"{source.provider}|{source.provider_user_id}|{source.external_activity_id}"
    ).lower()


def source_identity_key(contract: CanonicalExternalActivityV1) -> str:
    """Composite identity key for one external activity across imports."""
    return f"external-activity-{_stable_hash(source_identity_seed(contract))}"


def activity_payload_fingerprint(contract: CanonicalExternalActivityV1) -> str:
    """Stable fingerprint for overlap checks, excluding import timestamps."""
    payload = contract.model_dump(mode="json")
    payload.get("source", {}).pop("imported_at", None)
    payload.get("provenance", {}).pop("mapped_at", None)
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _stable_hash(canonical_json)


def build_external_idempotency_key(contract: CanonicalExternalActivityV1) -> str:
    """Replay-safe idempotency key for writes derived from source identity."""
    source = contract.source
    version_anchor = (
        source.external_event_version.strip().lower()
        if source.external_event_version
        else activity_payload_fingerprint(contract)
    )
    identity_suffix = _stable_hash(source_identity_seed(contract), size=16)
    version_suffix = _stable_hash(version_anchor, size=16)
    return f"external-import-{identity_suffix}-{version_suffix}"


def _try_parse_int(value: str) -> int | None:
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    return None


def _try_parse_datetime(value: str) -> datetime | None:
    raw = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _compare_versions(left: str, right: str) -> int:
    """Compare two version strings.

    Ordering strategy:
    1) integer-like versions (e.g. 7, 12)
    2) ISO datetime versions
    3) lexical fallback
    """
    left_int = _try_parse_int(left)
    right_int = _try_parse_int(right)
    if left_int is not None and right_int is not None:
        return (left_int > right_int) - (left_int < right_int)

    left_dt = _try_parse_datetime(left)
    right_dt = _try_parse_datetime(right)
    if left_dt is not None and right_dt is not None:
        return (left_dt > right_dt) - (left_dt < right_dt)

    left_norm = left.strip().lower()
    right_norm = right.strip().lower()
    return (left_norm > right_norm) - (left_norm < right_norm)


def _latest_known_version(existing_records: list[ExistingImportRecord]) -> str | None:
    latest: str | None = None
    for record in existing_records:
        raw_version = (record.external_event_version or "").strip()
        if not raw_version:
            continue
        if latest is None or _compare_versions(raw_version, latest) > 0:
            latest = raw_version
    return latest


def evaluate_duplicate_policy(
    *,
    candidate_version: str | None,
    candidate_payload_fingerprint: str,
    existing_records: list[ExistingImportRecord],
) -> DedupResult:
    """Decide dedup action for one incoming import candidate.

    Outcomes:
    - exact duplicate: skip write
    - version update: accept as update
    - stale version: reject
    - version conflict: reject (same version, different payload)
    - partial overlap: reject (no explicit version, changed payload)
    """
    if not existing_records:
        return DedupResult(
            decision="create",
            outcome="new_activity",
            reason="First observed import for this source identity.",
        )

    normalized_candidate_version = candidate_version.strip() if candidate_version else None
    if normalized_candidate_version:
        same_version = [
            record
            for record in existing_records
            if (record.external_event_version or "").strip() == normalized_candidate_version
        ]
        if same_version:
            if any(
                record.payload_fingerprint == candidate_payload_fingerprint
                for record in same_version
            ):
                return DedupResult(
                    decision="skip",
                    outcome="exact_duplicate",
                    reason="Matching source identity, version, and payload fingerprint.",
                )
            return DedupResult(
                decision="reject",
                outcome="version_conflict",
                reason=(
                    "Same external_event_version with different payload fingerprint; "
                    "manual conflict resolution required."
                ),
            )

        newest = _latest_known_version(existing_records)
        if newest is not None:
            comparison = _compare_versions(
                normalized_candidate_version,
                newest,
            )
            if comparison < 0:
                return DedupResult(
                    decision="reject",
                    outcome="stale_version",
                    reason=(
                        "Incoming external_event_version is older than latest known version."
                    ),
                )
            if comparison > 0:
                return DedupResult(
                    decision="update",
                    outcome="version_update",
                    reason="Incoming external_event_version is newer than latest known version.",
                )

        if any(
            record.payload_fingerprint == candidate_payload_fingerprint
            for record in existing_records
        ):
            return DedupResult(
                decision="skip",
                outcome="exact_duplicate",
                reason=(
                    "Version differs but payload fingerprint already imported; treated as replay."
                ),
            )

        return DedupResult(
            decision="update",
            outcome="version_update",
            reason=(
                "Version marker changed without directly comparable baseline; accept as update."
            ),
        )

    if any(
        record.payload_fingerprint == candidate_payload_fingerprint
        for record in existing_records
    ):
        return DedupResult(
            decision="skip",
            outcome="exact_duplicate",
            reason="No version provided but payload fingerprint already imported.",
        )

    return DedupResult(
        decision="reject",
        outcome="partial_overlap",
        reason=(
            "No external_event_version provided and payload changed; overlap cannot be "
            "resolved safely."
        ),
    )
