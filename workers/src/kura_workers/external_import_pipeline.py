"""External import pipeline core (tm5.6).

Flow: parse -> map -> validate -> dedup -> write-plan
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import ValidationError

from .external_activity_contract import (
    CanonicalExternalActivityV1,
    validate_external_activity_contract_v1,
)
from .external_identity import (
    DedupResult,
    ExistingImportRecord,
    activity_payload_fingerprint,
    build_external_idempotency_key,
    evaluate_duplicate_policy,
    source_identity_key,
)
from .external_mapping_matrix import map_external_payload_v1

ImportFormat = Literal["fit", "tcx", "gpx"]
IngestionMethod = Literal["file_import", "connector_api", "manual_backfill"]

_KJ_TO_KCAL = 0.239005736


class ImportPipelineError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        field: str | None = None,
        docs_hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.docs_hint = docs_hint


@dataclass(frozen=True)
class ParsedImportActivity:
    workout_type: str
    started_at: str
    ended_at: str
    duration_seconds: float
    distance_meters: float | None
    calories_kcal: float | None
    timezone: str | None
    heart_rate_avg: float | None
    heart_rate_max: float | None
    power_watt: float | None
    pace_min_per_km: float | None
    session_rpe: float | None


@dataclass(frozen=True)
class ImportPlan:
    canonical_activity: CanonicalExternalActivityV1
    dedup_result: DedupResult
    source_identity_key: str
    payload_fingerprint: str
    idempotency_key: str
    mapping_version: str
    unsupported_fields: list[str]
    warnings: list[str]

    @property
    def should_write(self) -> bool:
        return self.dedup_result.decision in {"create", "update"}


def _parse_iso(value: str, *, field: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ImportPipelineError(
            code="parse_error",
            message=f"Invalid timestamp for {field}: {value}",
            field=field,
            docs_hint="Use ISO 8601 timestamps (e.g. 2026-02-12T08:00:00+00:00).",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_child_text(element: ET.Element, child_name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == child_name and child.text:
            return child.text.strip()
    return None


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fit_payload(payload_text: str) -> ParsedImportActivity:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ImportPipelineError(
            code="parse_error",
            message="FIT import expects JSON payload in v1.",
            field="payload_text",
            docs_hint=(
                "Provide FIT payload as JSON export with start_time and duration_seconds."
            ),
        ) from exc

    if not isinstance(payload, dict):
        raise ImportPipelineError(
            code="parse_error",
            message="FIT payload must decode to an object.",
            field="payload_text",
            docs_hint="Use JSON object payload for FIT v1 import.",
        )

    start_time_raw = payload.get("start_time")
    duration_raw = payload.get("duration_seconds", payload.get("duration_s"))
    if start_time_raw is None or duration_raw is None:
        raise ImportPipelineError(
            code="parse_error",
            message="FIT payload missing start_time or duration_seconds.",
            field="payload_text",
            docs_hint="Include start_time and duration_seconds in FIT JSON payload.",
        )

    started_at = _parse_iso(str(start_time_raw), field="start_time")
    duration_seconds = float(duration_raw)
    ended_at = started_at + timedelta(seconds=duration_seconds)

    calories = payload.get("calories_kcal")
    if calories is None and payload.get("energy_kj") is not None:
        calories = float(payload["energy_kj"]) * _KJ_TO_KCAL

    distance = payload.get("distance_meters", payload.get("distance_m"))
    heart_rate_avg = _safe_float(payload.get("heart_rate_avg", payload.get("avg_hr")))
    heart_rate_max = _safe_float(payload.get("heart_rate_max", payload.get("max_hr")))
    power_watt = _safe_float(payload.get("power_watt", payload.get("power_avg")))
    session_rpe = _safe_float(payload.get("session_rpe", payload.get("rpe")))
    pace_min_per_km = _safe_float(payload.get("pace_min_per_km"))
    if (
        pace_min_per_km is None
        and distance is not None
        and float(distance) > 0
        and duration_seconds > 0
    ):
        pace_min_per_km = (duration_seconds / 60.0) / (float(distance) / 1000.0)
    return ParsedImportActivity(
        workout_type=str(payload.get("sport", "workout")).strip().lower() or "workout",
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        duration_seconds=duration_seconds,
        distance_meters=float(distance) if distance is not None else None,
        calories_kcal=float(calories) if calories is not None else None,
        timezone=str(payload.get("timezone")).strip() if payload.get("timezone") else None,
        heart_rate_avg=heart_rate_avg,
        heart_rate_max=heart_rate_max,
        power_watt=power_watt,
        pace_min_per_km=pace_min_per_km,
        session_rpe=session_rpe,
    )


def _parse_tcx_payload(payload_text: str) -> ParsedImportActivity:
    try:
        root = ET.fromstring(payload_text)
    except ET.ParseError as exc:
        raise ImportPipelineError(
            code="parse_error",
            message="TCX XML parsing failed.",
            field="payload_text",
            docs_hint="Provide valid TCX XML content.",
        ) from exc

    activity = None
    for elem in root.iter():
        if _local_name(elem.tag) == "Activity":
            activity = elem
            break

    if activity is None:
        raise ImportPipelineError(
            code="parse_error",
            message="TCX payload missing Activity node.",
            field="payload_text",
            docs_hint="TCX must include Activities/Activity elements.",
        )

    sport = str(activity.attrib.get("Sport", "workout")).strip().lower() or "workout"

    start_raw = None
    for child in activity:
        if _local_name(child.tag) == "Id" and child.text:
            start_raw = child.text.strip()
            break
    if not start_raw:
        raise ImportPipelineError(
            code="parse_error",
            message="TCX Activity missing Id timestamp.",
            field="payload_text",
            docs_hint="TCX Activity must include an Id timestamp.",
        )

    started_at = _parse_iso(start_raw, field="activity.id")

    total_seconds = 0.0
    distance_meters = 0.0
    calories_kcal = 0.0
    lap_count = 0
    avg_hr_samples: list[float] = []
    max_hr_samples: list[float] = []
    power_samples: list[float] = []

    for elem in activity.iter():
        if _local_name(elem.tag) != "Lap":
            continue
        lap_count += 1
        sec = _find_child_text(elem, "TotalTimeSeconds")
        dist = _find_child_text(elem, "DistanceMeters")
        cal = _find_child_text(elem, "Calories")
        if sec:
            total_seconds += float(sec)
        if dist:
            distance_meters += float(dist)
        if cal:
            calories_kcal += float(cal)
        avg_hr = None
        max_hr = None
        for child in elem:
            child_name = _local_name(child.tag)
            if child_name == "AverageHeartRateBpm":
                avg_hr = _find_child_text(child, "Value")
            elif child_name == "MaximumHeartRateBpm":
                max_hr = _find_child_text(child, "Value")
        if avg_hr:
            parsed = _safe_float(avg_hr)
            if parsed is not None:
                avg_hr_samples.append(parsed)
        if max_hr:
            parsed = _safe_float(max_hr)
            if parsed is not None:
                max_hr_samples.append(parsed)
        for nested in elem.iter():
            tag_name = _local_name(nested.tag).lower()
            if tag_name in {"watts", "avgwatts", "averagewatts", "power"} and nested.text:
                parsed = _safe_float(nested.text)
                if parsed is not None:
                    power_samples.append(parsed)

    if lap_count == 0 or total_seconds <= 0:
        raise ImportPipelineError(
            code="parse_error",
            message="TCX payload has no Lap duration data.",
            field="payload_text",
            docs_hint="Ensure TCX laps include TotalTimeSeconds.",
        )

    ended_at = started_at + timedelta(seconds=total_seconds)
    pace_min_per_km = None
    if distance_meters > 0:
        pace_min_per_km = (total_seconds / 60.0) / (distance_meters / 1000.0)

    heart_rate_avg = (
        (sum(avg_hr_samples) / len(avg_hr_samples))
        if avg_hr_samples
        else None
    )
    heart_rate_max = max(max_hr_samples) if max_hr_samples else None
    power_watt = (sum(power_samples) / len(power_samples)) if power_samples else None

    return ParsedImportActivity(
        workout_type=sport,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        duration_seconds=total_seconds,
        distance_meters=distance_meters if distance_meters > 0 else None,
        calories_kcal=calories_kcal if calories_kcal > 0 else None,
        timezone="UTC",
        heart_rate_avg=heart_rate_avg,
        heart_rate_max=heart_rate_max,
        power_watt=power_watt,
        pace_min_per_km=pace_min_per_km,
        session_rpe=None,
    )


def _parse_gpx_payload(payload_text: str) -> ParsedImportActivity:
    try:
        root = ET.fromstring(payload_text)
    except ET.ParseError as exc:
        raise ImportPipelineError(
            code="parse_error",
            message="GPX XML parsing failed.",
            field="payload_text",
            docs_hint="Provide valid GPX XML content.",
        ) from exc

    times: list[datetime] = []
    max_distance: float | None = None

    for elem in root.iter():
        name = _local_name(elem.tag)
        if name == "time" and elem.text:
            times.append(_parse_iso(elem.text, field="gpx.time"))
        if name in {"distance", "DistanceMeters"} and elem.text:
            try:
                value = float(elem.text.strip())
            except ValueError:
                continue
            max_distance = value if max_distance is None else max(max_distance, value)

    if len(times) < 2:
        raise ImportPipelineError(
            code="parse_error",
            message="GPX payload needs at least two trackpoint timestamps.",
            field="payload_text",
            docs_hint="Ensure GPX has trkpt/time entries.",
        )

    started_at = min(times)
    ended_at = max(times)
    duration_seconds = max((ended_at - started_at).total_seconds(), 0.0)
    pace_min_per_km = None
    if max_distance is not None and max_distance > 0 and duration_seconds > 0:
        pace_min_per_km = (duration_seconds / 60.0) / (max_distance / 1000.0)

    return ParsedImportActivity(
        workout_type="run",
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        duration_seconds=duration_seconds,
        distance_meters=max_distance,
        calories_kcal=None,
        timezone="UTC",
        heart_rate_avg=None,
        heart_rate_max=None,
        power_watt=None,
        pace_min_per_km=pace_min_per_km,
        session_rpe=None,
    )


def parse_import_payload(file_format: ImportFormat, payload_text: str) -> ParsedImportActivity:
    normalized_format = file_format.lower()
    if normalized_format == "fit":
        return _parse_fit_payload(payload_text)
    if normalized_format == "tcx":
        return _parse_tcx_payload(payload_text)
    if normalized_format == "gpx":
        return _parse_gpx_payload(payload_text)
    raise ImportPipelineError(
        code="unsupported_format",
        message=f"Unsupported import format: {file_format}",
        field="file_format",
        docs_hint="Supported formats are fit, tcx, gpx.",
    )


def _provider_payload(provider: str, parsed: ParsedImportActivity) -> dict[str, object]:
    provider_name = provider.lower()
    energy_kj = None
    if parsed.calories_kcal is not None:
        energy_kj = parsed.calories_kcal / _KJ_TO_KCAL

    if provider_name == "garmin":
        return {
            "activity": {
                "type": parsed.workout_type,
                "start_time": parsed.started_at,
                "end_time": parsed.ended_at,
                "timezone": parsed.timezone or "UTC",
            },
            "summary": {
                "duration_s": parsed.duration_seconds,
                "distance_m": parsed.distance_meters,
                "energy_kj": energy_kj,
                "heart_rate_avg": parsed.heart_rate_avg,
                "heart_rate_max": parsed.heart_rate_max,
                "power_watt": parsed.power_watt,
                "pace_min_per_km": parsed.pace_min_per_km,
                "session_rpe": parsed.session_rpe,
            },
        }
    if provider_name == "strava":
        return {
            "type": parsed.workout_type,
            "moving_time": parsed.duration_seconds,
            "distance": parsed.distance_meters,
            "kilojoules": energy_kj,
            "start_date": parsed.started_at,
            "timezone": parsed.timezone or "UTC",
            "average_heartrate": parsed.heart_rate_avg,
            "max_heartrate": parsed.heart_rate_max,
            "average_watts": parsed.power_watt,
            "pace_min_per_km": parsed.pace_min_per_km,
            "perceived_exertion": parsed.session_rpe,
        }
    if provider_name == "trainingpeaks":
        return {
            "workout": {
                "type": parsed.workout_type,
                "totalTimeMinutes": parsed.duration_seconds / 60.0,
                "distanceKm": (
                    parsed.distance_meters / 1000.0
                    if parsed.distance_meters is not None
                    else None
                ),
                "energyKj": energy_kj,
                "startTime": parsed.started_at,
                "endTime": parsed.ended_at,
                "timezone": parsed.timezone or "UTC",
                "avgHr": parsed.heart_rate_avg,
                "maxHr": parsed.heart_rate_max,
                "avgPower": parsed.power_watt,
                "paceMinPerKm": parsed.pace_min_per_km,
                "sessionRpe": parsed.session_rpe,
            }
        }
    raise ImportPipelineError(
        code="mapping_error",
        message=f"Unsupported provider: {provider}",
        field="provider",
        docs_hint="Supported providers are garmin, strava, trainingpeaks.",
    )


def build_import_plan(
    *,
    provider: str,
    provider_user_id: str,
    external_activity_id: str,
    file_format: ImportFormat,
    payload_text: str,
    external_event_version: str | None,
    existing_records: list[ExistingImportRecord],
    ingestion_method: IngestionMethod = "file_import",
) -> ImportPlan:
    parsed = parse_import_payload(file_format, payload_text)
    mapped = map_external_payload_v1(
        provider=provider,
        provider_user_id=provider_user_id,
        external_activity_id=external_activity_id,
        external_event_version=external_event_version,
        raw_payload=_provider_payload(provider, parsed),
        ingestion_method=ingestion_method,
    )
    try:
        canonical = validate_external_activity_contract_v1(mapped.canonical_draft)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        raise ImportPipelineError(
            code="validation_error",
            message=first.get("msg", "Canonical contract validation failed."),
            field=".".join(str(part) for part in first.get("loc", [])) or "canonical_draft",
            docs_hint="Ensure mapped payload satisfies external_activity.v1 requirements.",
        ) from exc

    payload_fingerprint = activity_payload_fingerprint(canonical)
    dedup = evaluate_duplicate_policy(
        candidate_version=canonical.source.external_event_version,
        candidate_payload_fingerprint=payload_fingerprint,
        existing_records=existing_records,
    )
    return ImportPlan(
        canonical_activity=canonical,
        dedup_result=dedup,
        source_identity_key=source_identity_key(canonical),
        payload_fingerprint=payload_fingerprint,
        idempotency_key=build_external_idempotency_key(canonical),
        mapping_version=mapped.canonical_draft["provenance"]["mapping_version"],
        unsupported_fields=mapped.unsupported_fields,
        warnings=mapped.warnings,
    )
